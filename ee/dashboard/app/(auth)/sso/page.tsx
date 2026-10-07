// Proprietary: see ee/LICENSE
import type { Metadata } from "next";
import { Suspense } from "react";
import { SsoForm } from "@/components/AuthForms";

export const metadata: Metadata = { title: "Single sign-on" };

export default function Page() {
  return (
    <Suspense>
      <SsoForm />
    </Suspense>
  );
}
