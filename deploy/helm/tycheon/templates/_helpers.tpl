{{/* Names and labels */}}
{{- define "tycheon.name" -}}tycheon{{- end -}}

{{- define "tycheon.fullname" -}}
{{- if .Values.fullnameOverride -}}{{ .Values.fullnameOverride | trunc 40 | trimSuffix "-" }}
{{- else if contains "tycheon" .Release.Name -}}{{ .Release.Name | trunc 40 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-tycheon" .Release.Name | trunc 40 | trimSuffix "-" }}{{- end -}}
{{- end -}}

{{- define "tycheon.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "tycheon.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.global.labels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "tycheon.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tycheon.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* image reference: registry/repository:tag, or @digest when one is pinned */}}
{{- define "tycheon.image" -}}
{{- $root := .root -}}{{- $img := .image -}}
{{- $repo := printf "%s/%s" $root.Values.global.imageRegistry $img.repository -}}
{{- if $img.digest -}}{{ printf "%s@%s" $repo $img.digest }}
{{- else -}}{{ printf "%s:%s%s" $repo ($img.tag | default $root.Chart.AppVersion) (default "" .suffix) }}{{- end -}}
{{- end -}}

{{/* the pod and container security contexts every workload uses */}}
{{- define "tycheon.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "tycheon.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}

{{- define "tycheon.pullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
{{ toYaml . | indent 2 }}
{{- end }}
{{- end -}}

{{/* OpenTelemetry environment shared by every workload */}}
{{- define "tycheon.otelEnv" -}}
{{- if .Values.otel.enabled }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: "http://{{ include "tycheon.fullname" . }}-otel:4317"
- name: OTEL_EXPORTER_OTLP_PROTOCOL
  value: grpc
- name: OTEL_SERVICE_NAME
  value: {{ .Values.telemetry.serviceName | quote }}
{{- end }}
{{- end -}}

{{/*
A complete workload: ServiceAccount, Deployment, Service, optional HPA and PodDisruptionBudget.
Arguments (dict): root, component, cfg (the component's values), image, port (0 = no Service),
command, args, probePath, envFromSecrets (list), env (map), extraVolumes, extraMounts, gpu (bool)
*/}}
{{- define "tycheon.workload" -}}
{{- $root := .root -}}{{- $c := .cfg -}}{{- $name := printf "%s-%s" (include "tycheon.fullname" .root) .component }}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ $name }}
  labels: {{- include "tycheon.labels" $root | nindent 4 }}
  {{- with (index $root.Values.serviceAccount.annotations (.saKey | default .component)) }}
  annotations: {{- toYaml . | nindent 4 }}
  {{- end }}
automountServiceAccountToken: false
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $name }}
  labels:
    {{- include "tycheon.labels" $root | nindent 4 }}
    app.kubernetes.io/component: {{ .component }}
spec:
  {{- if not (and $c.autoscaling $c.autoscaling.enabled) }}
  replicas: {{ $c.replicas | default 1 }}
  {{- end }}
  revisionHistoryLimit: 5
  strategy:
    type: RollingUpdate
    rollingUpdate: { maxUnavailable: 0, maxSurge: 1 }
  selector:
    matchLabels: {{- include "tycheon.selectorLabels" (dict "root" $root "component" .component) | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "tycheon.labels" $root | nindent 8 }}
        app.kubernetes.io/component: {{ .component }}
      annotations:
        {{- with $root.Values.podAnnotations }}{{ toYaml . | nindent 8 }}{{ end }}
        {{- if .checksum }}
        checksum/config: {{ .checksum }}
        {{- end }}
    spec:
      serviceAccountName: {{ $name }}
      automountServiceAccountToken: false
      enableServiceLinks: false
      terminationGracePeriodSeconds: {{ .grace | default 30 }}
      securityContext: {{- include "tycheon.podSecurityContext" $root | nindent 8 }}
      {{- include "tycheon.pullSecrets" $root | nindent 6 }}
      {{- with $c.nodeSelector }}
      nodeSelector: {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $c.tolerations }}
      tolerations: {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- if $c.affinity }}
      affinity: {{- toYaml $c.affinity | nindent 8 }}
      {{- end }}
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels: {{- include "tycheon.selectorLabels" (dict "root" $root "component" .component) | nindent 14 }}
      containers:
        - name: {{ .component }}
          image: {{ .image | quote }}
          imagePullPolicy: {{ $root.Values.image.pullPolicy }}
          {{- with .command }}
          command: {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .args }}
          args: {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- if .port }}
          ports:
            - { name: http, containerPort: {{ .port }}, protocol: TCP }
          {{- end }}
          env:
            {{- range $k, $v := .env }}
            - { name: {{ $k }}, value: {{ $v | quote }} }
            {{- end }}
            {{- include "tycheon.otelEnv" $root | nindent 12 }}
          {{- if .envFromSecrets }}
          envFrom:
            {{- range .envFromSecrets }}
            - secretRef: { name: {{ . }} }
            {{- end }}
          {{- end }}
          securityContext: {{- include "tycheon.containerSecurityContext" $root | nindent 12 }}
          resources: {{- toYaml $c.resources | nindent 12 }}
          {{- if and .port .probePath }}
          startupProbe:
            httpGet: { path: {{ .probePath }}, port: http }
            periodSeconds: 5
            failureThreshold: {{ .startupFailures | default 36 }}
          readinessProbe:
            httpGet: { path: {{ .probePath }}, port: http }
            periodSeconds: 10
            timeoutSeconds: 3
          livenessProbe:
            httpGet: { path: {{ .probePath }}, port: http }
            periodSeconds: 20
            timeoutSeconds: 5
            failureThreshold: 3
          {{- end }}
          volumeMounts:
            - { name: tmp, mountPath: /tmp }
            {{- with .extraMounts }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
      volumes:
        - { name: tmp, emptyDir: { sizeLimit: 512Mi } }
        {{- with .extraVolumes }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
{{- if .port }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $name }}
  labels:
    {{- include "tycheon.labels" $root | nindent 4 }}
    app.kubernetes.io/component: {{ .component }}
spec:
  type: ClusterIP
  selector: {{- include "tycheon.selectorLabels" (dict "root" $root "component" .component) | nindent 4 }}
  ports:
    - { name: http, port: {{ .port }}, targetPort: http, protocol: TCP }
{{- end }}
{{- if and $c.autoscaling $c.autoscaling.enabled }}
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ $name }}
  labels: {{- include "tycheon.labels" $root | nindent 4 }}
spec:
  scaleTargetRef: { apiVersion: apps/v1, kind: Deployment, name: {{ $name }} }
  minReplicas: {{ $c.autoscaling.minReplicas }}
  maxReplicas: {{ $c.autoscaling.maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: { type: Utilization, averageUtilization: {{ $c.autoscaling.targetCPUUtilizationPercentage }} }
  behavior:
    scaleDown: { stabilizationWindowSeconds: 300 }
{{- end }}
{{- if and $c.pdb (or (gt (int ($c.replicas | default 1)) 1) (and $c.autoscaling $c.autoscaling.enabled)) }}
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ $name }}
  labels: {{- include "tycheon.labels" $root | nindent 4 }}
spec:
  minAvailable: {{ $c.pdb.minAvailable }}
  selector:
    matchLabels: {{- include "tycheon.selectorLabels" (dict "root" $root "component" .component) | nindent 6 }}
{{- end }}
{{- end -}}
