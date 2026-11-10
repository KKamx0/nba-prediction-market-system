# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/KKamx0/nba-prediction-market-system/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                   |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| src/edgebot/\_\_init\_\_.py            |        1 |        0 |        0 |        0 |    100% |           |
| src/edgebot/cli.py                     |      318 |       68 |       40 |        8 |     75% |37-43, 47-51, 55-59, 88-98, 126, 133-134, 164-165, 232-233, 244-245, 278-314, 338-346, 371-372, 503 |
| src/edgebot/config.py                  |       59 |        1 |       10 |        2 |     96% |59, 110-\>109 |
| src/edgebot/db.py                      |       84 |        0 |        4 |        0 |    100% |           |
| src/edgebot/devig.py                   |       44 |        4 |       16 |        3 |     85% |29, 50-\>58, 67-69 |
| src/edgebot/doctor.py                  |       67 |        0 |        8 |        1 |     99% |   89-\>92 |
| src/edgebot/gate.py                    |       49 |        0 |        4 |        0 |    100% |           |
| src/edgebot/ingest/\_\_init\_\_.py     |        0 |        0 |        0 |        0 |    100% |           |
| src/edgebot/ingest/historical\_odds.py |      103 |       18 |       34 |        8 |     75% |71-75, 80, 97, 130, 134-135, 141, 156, 173, 177-182 |
| src/edgebot/ingest/http.py             |       51 |        8 |        6 |        0 |     82% |55, 58, 65-69, 84 |
| src/edgebot/ingest/injuries.py         |      106 |       14 |       36 |        3 |     85% |69-\>66, 85, 132-138, 143-144, 147, 150-153, 170-\>173 |
| src/edgebot/ingest/nba\_stats.py       |      131 |       13 |       40 |        4 |     88% |40-47, 84, 111-112, 116-117, 240 |
| src/edgebot/ingest/odds\_api.py        |       72 |        3 |       18 |        2 |     94% |70, 75-76, 129-\>131 |
| src/edgebot/ingest/polymarket.py       |      115 |        4 |       30 |        5 |     94% |61, 80, 141, 154, 182-\>178 |
| src/edgebot/learning/\_\_init\_\_.py   |        0 |        0 |        0 |        0 |    100% |           |
| src/edgebot/learning/minutes\_bias.py  |       34 |        0 |        6 |        0 |    100% |           |
| src/edgebot/learning/postmortem.py     |       47 |        1 |       18 |        1 |     97% |        58 |
| src/edgebot/learning/retrain.py        |       76 |       11 |       18 |        4 |     82% |44-51, 78-\>84, 116, 120, 129 |
| src/edgebot/learning/segments.py       |       92 |        0 |       20 |        0 |    100% |           |
| src/edgebot/learning/summary.py        |       47 |        4 |       12 |        2 |     90% |64-65, 70-71 |
| src/edgebot/model/\_\_init\_\_.py      |        0 |        0 |        0 |        0 |    100% |           |
| src/edgebot/model/backtest.py          |      138 |        8 |       48 |        7 |     92% |61, 63, 67-68, 77, 111, 113-118, 233-\>231 |
| src/edgebot/model/features.py          |      129 |        1 |       20 |        1 |     99% |       135 |
| src/edgebot/model/game\_model.py       |       85 |        0 |       10 |        0 |    100% |           |
| src/edgebot/model/metrics.py           |       31 |        0 |        6 |        0 |    100% |           |
| src/edgebot/model/minutes.py           |      120 |        2 |       32 |        4 |     96% |105, 121-\>134, 130, 231-\>224 |
| src/edgebot/model/ratings.py           |       99 |        0 |       32 |        2 |     98% |88-\>87, 126-\>125 |
| src/edgebot/model/strength.py          |       54 |        0 |        4 |        0 |    100% |           |
| src/edgebot/names.py                   |       14 |        0 |        2 |        0 |    100% |           |
| src/edgebot/news.py                    |      166 |       10 |       42 |        4 |     93% |206-208, 238, 256, 336-340, 346 |
| src/edgebot/ratelimit.py               |       26 |        0 |        4 |        0 |    100% |           |
| src/edgebot/report.py                  |      119 |       18 |       22 |        3 |     84% |81-\>78, 189-207, 236 |
| src/edgebot/runner.py                  |      215 |       11 |       56 |        8 |     92% |200-\>202, 205-\>exit, 208-\>exit, 271-277, 298-\>307, 346, 366, 391, 399, 406 |
| src/edgebot/teams.py                   |       46 |        0 |        4 |        0 |    100% |           |
| src/edgebot/timeutil.py                |       22 |        0 |        2 |        0 |    100% |           |
| src/edgebot/trading/\_\_init\_\_.py    |        0 |        0 |        0 |        0 |    100% |           |
| src/edgebot/trading/decision.py        |      112 |       10 |       32 |        7 |     88% |68, 113, 139, 142-145, 184-\>190, 188-189, 191-193 |
| src/edgebot/trading/edge.py            |       66 |        0 |       12 |        0 |    100% |           |
| src/edgebot/trading/executor.py        |       64 |        0 |        4 |        0 |    100% |           |
| src/edgebot/trading/live.py            |       91 |       17 |       18 |        1 |     78% |   146-174 |
| src/edgebot/trading/risk.py            |      106 |        0 |       26 |        0 |    100% |           |
| src/edgebot/trading/settlement.py      |       27 |        1 |        8 |        1 |     94% |        18 |
| src/edgebot/trading/sizing.py          |       18 |        1 |        4 |        1 |     91% |        29 |
| src/edgebot/trading/state.py           |       18 |        0 |        2 |        0 |    100% |           |
| **TOTAL**                              | **3262** |  **228** |  **710** |   **82** | **91%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/KKamx0/nba-prediction-market-system/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/KKamx0/nba-prediction-market-system/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/KKamx0/nba-prediction-market-system/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/KKamx0/nba-prediction-market-system/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2FKKamx0%2Fnba-prediction-market-system%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/KKamx0/nba-prediction-market-system/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.