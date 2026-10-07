// Proprietary: see ee/LICENSE
import type { Metadata } from "next";
import Overview from "@/components/Overview";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Overview" };

export default function Page() {
  return (
    <>
      <PageTitle>Overview</PageTitle>
      <Overview />
    </>
  );
}
