// Proprietary: see ee/LICENSE
import type { ForecastOut } from "@/lib/api";
import { StatusBadge } from "./ui";

export const MODELS = ["routed", "random-walk", "drift", "garch", "kronos-mini"];

export function CalibrationBadge({ status }: { status: ForecastOut["calibration_status"] }) {
  if (status === "calibrated") return <StatusBadge tone="ok">Calibrated</StatusBadge>;
  if (status === "stale") return <StatusBadge tone="warn">Calibration stale</StatusBadge>;
  return <StatusBadge tone="bad">UNCALIBRATED</StatusBadge>;
}
