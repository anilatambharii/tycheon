# GitHub Discussions: categories to create by hand

**For research and risk analytics. Not investment advice.**

Discussions cannot be created or configured from files. The repository currently has them switched off (`has_discussions` was `false` when this was written), so this page is a checklist for the maintainer. The category forms already exist in `.github/DISCUSSION_TEMPLATE/`; GitHub uses a form when its file name matches the category **slug**, so the slugs below must be exactly as written.

## Steps

1. Repository Settings, General, Features: tick **Discussions**.
2. Open the Discussions tab, then the pencil icon next to Categories. Delete the default categories you do not want and create the ones in the table.
3. Check that the slug GitHub generates (the last part of the category URL) matches the table. If GitHub makes a different slug, rename the file in `.github/DISCUSSION_TEMPLATE/` to match, or rename the category.
4. Post and pin the welcome message below in **Announcements**.
5. `.github/ISSUE_TEMPLATE/config.yml` already links to `https://github.com/anilatambharii/tycheon/discussions`; that link only works after step 1.

## Categories

| Category name | Slug (must match the file) | Format | Form file | Purpose |
|---|---|---|---|---|
| Announcements | `announcements` | Announcement (maintainers post) | none | Releases, benchmark updates, breaking changes |
| Q&A | `q-a` | Question and answer | `.github/DISCUSSION_TEMPLATE/q-a.yml` | How do I use X; mark an answer |
| Ideas | `ideas` | Open-ended | `.github/DISCUSSION_TEMPLATE/ideas.yml` | Proposals before they become issues |
| Show and tell | `show-and-tell` | Open-ended | `.github/DISCUSSION_TEMPLATE/show-and-tell.yml` | What you built, with the data and the baseline stated |
| Benchmarks | `benchmarks` | Open-ended | `.github/DISCUSSION_TEMPLATE/benchmarks.yml` | Reading results, methodology, reproductions, requests to be measured |

Optional later: a **General** category, if the five above prove too narrow. Security reports never belong in Discussions; the forms say so and `SECURITY.md` has the private route.

## Welcome message (pin it in Announcements)

> **Welcome to Tycheon Discussions.**
>
> Tycheon is open-source calibrated forecasting and risk for financial time series. *Kronos forecasts the path; Tycheon tells you how much to trust it.*
>
> - **Questions about using it:** Q&A.
> - **Ideas before they are issues:** Ideas.
> - **Something you built:** Show and tell. Say which data you used and how it did against the random walk, including when the baseline won. That is welcome here.
> - **Reading or reproducing a benchmark result:** Benchmarks. Quote numbers from the file in `benchmarks/results/`.
> - **Bugs:** open an issue with the bug form. **Security problems, including anything that lets future data leak into a forecast:** do not post publicly; follow `SECURITY.md`.
>
> What this is not: a place for trade tips. Tycheon is for research and risk analytics and is not investment advice, and we will not discuss what to buy or sell.
>
> Please read the [Code of Conduct](https://github.com/anilatambharii/tycheon/blob/main/CODE_OF_CONDUCT.md) and [CONTRIBUTING](https://github.com/anilatambharii/tycheon/blob/main/CONTRIBUTING.md).

## Moderation notes

- Move bug reports to issues, and move anything that looks like a lookahead or governance bypass to a private advisory immediately (edit the post to remove reproduction details if they describe an exploit).
- Do not allow posting of licensed market data; Tycheon does not redistribute it and neither should the community channel.
- Close threads that ask for personalized investment advice with a pointer to the disclaimer.
