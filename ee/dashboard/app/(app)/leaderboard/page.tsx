// Proprietary: see ee/LICENSE
import type { Metadata } from "next";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Leaderboard" };

const DEFAULT_URL = "https://anilatambharii.github.io/tycheon/leaderboard/";

function leaderboardUrl(): string {
  const raw = process.env.NEXT_PUBLIC_LEADERBOARD_URL || DEFAULT_URL;
  try {
    const u = new URL(raw);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : DEFAULT_URL;
  } catch {
    return DEFAULT_URL;
  }
}

export default function Page() {
  const url = leaderboardUrl();
  return (
    <>
      <PageTitle>Leaderboard</PageTitle>
      <p className="mb-3 text-sm">
        Public, leakage-checked walk-forward results, including cases where the random-walk baseline wins.{" "}
        <a href={url} target="_blank" rel="noopener noreferrer">
          Open the leaderboard in a new tab
        </a>
        .
      </p>
      <iframe
        title="Tycheon public leaderboard"
        src={url}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        loading="lazy"
        className="h-[75vh] w-full rounded-md border"
        style={{ borderColor: "var(--border)", background: "#fff" }}
      />
    </>
  );
}
