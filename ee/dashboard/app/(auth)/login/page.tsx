// Proprietary: see ee/LICENSE
import type { Metadata } from "next";
import { Suspense } from "react";
import { LoginForm } from "@/components/AuthForms";

export const metadata: Metadata = { title: "Sign in" };

export default function Page() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
