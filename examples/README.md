# Examples

Runnable, self-contained scripts. Each one states its data source and its
`as_of` up front.

Nothing here is a trading strategy, and nothing here is investment advice.
These examples exist to show the shape of the library: how a forecast carries
its uncertainty, how calibration is checked, and how a forecast becomes a risk
number.

## Data in examples

Examples default to the sample data that ships with the repo, so a fresh clone
runs offline.

`yfinance` may appear in examples and local development **only**, always
clearly labelled, and gated behind `TYCHEON_ALLOW_YFINANCE=true`. It is never
used in the cloud product. Market data providers are pluggable and you bring
your own data license; Tycheon never redistributes licensed exchange data.

Examples land alongside the features they demonstrate, starting in Phase T1.
