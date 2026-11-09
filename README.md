# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/KKamx0/nba-prediction-market-system/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                   |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| src/edgebot/\_\_init\_\_.py            |        1 |        0 |        0 |        0 |    100% |           |
| src/edgebot/cli.py                     |      277 |       64 |       36 |        7 |     73% |37-43, 47-51, 55-59, 88-98, 126, 133-134, 164-165, 194-195, 225-258, 281-289, 314-315, 437 |
| src/edgebot/config.py                  |       58 |        1 |       10 |        2 |     96% |58, 108-\>107 |
| src/edgebot/db.py                      |       84 |        0 |        4 |        0 |    100% |           |
| src/edgebot/devig.py                   |       44 |        4 |       16 |        3 |     85% |29, 50-\>58, 67-69 |
| src/edgebot/ingest/\_\_init\_\_.py     |        0 |        0 |        0 |        0 |    100% |           |
| src/edgebot/ingest/historical\_odds.py |      103 |       18 |       34 |        8 |     75% |71-75, 80, 97, 130, 134-135, 141, 156, 173, 177-182 |
| src/edgebot/ingest/http.py             |       51 |        8 |        6 |        0 |     82% |55, 58, 65-69, 84 |
| src/edgebot/ingest/injuries.py         |      106 |       14 |       36 |        3 |     85% |69-\>66, 85, 132-138, 143-144, 147, 150-153, 170-\>173 |
| src/edgebot/ingest/nba\_stats.py       |      131 |       13 |       40 |        4 |     88% |40-47, 84, 111-112, 116-117, 240 |
| src/edgebot/ingest/odds\_api.py        |       72 |        3 |       18 |        2 |     94% |70, 75-76, 129-\>131 |
| src/edgebot/ingest/polymarket.py       |      115 |        4 |       30 |        5 |     94% |61, 80, 141, 154, 182-\>178 |
| src/edgebot/model/\_\_init\_\_.py      |        0 |        0 |        0 |        0 |    100% |           |
| src/edgebot/model/backtest.py          |      136 |        7 |       46 |        6 |     93% |56, 60-61, 70, 104, 106-111, 226-\>224 |
| src/edgebot/model/features.py          |      118 |        1 |       16 |        1 |     99% |       123 |
| src/edgebot/model/game\_model.py       |       85 |        3 |       10 |        0 |     97% |   159-162 |
| src/edgebot/model/metrics.py           |       31 |        0 |        6 |        0 |    100% |           |
| src/edgebot/model/minutes.py           |      120 |        2 |       32 |        4 |     96% |105, 121-\>134, 130, 231-\>224 |
| src/edgebot/model/ratings.py           |       99 |        0 |       32 |        2 |     98% |88-\>87, 126-\>125 |
| src/edgebot/model/strength.py          |       54 |        0 |        4 |        0 |    100% |           |
| src/edgebot/names.py                   |       14 |        0 |        2 |        0 |    100% |           |
| src/edgebot/news.py                    |      166 |       10 |       42 |        4 |     93% |206-208, 238, 256, 336-340, 346 |
| src/edgebot/ratelimit.py               |       26 |        0 |        4 |        0 |    100% |           |
| src/edgebot/report.py                  |      119 |       18 |       22 |        3 |     84% |81-\>78, 179-197, 226 |
| src/edgebot/runner.py                  |      178 |       11 |       42 |        6 |     91% |210-\>222, 215-221, 238-\>242, 280, 300, 325, 333, 340 |
| src/edgebot/teams.py                   |       46 |        0 |        4 |        0 |    100% |           |
| src/edgebot/timeutil.py                |       22 |        0 |        2 |        0 |    100% |           |
| src/edgebot/trading/\_\_init\_\_.py    |        0 |        0 |        0 |        0 |    100% |           |
| src/edgebot/trading/decision.py        |      100 |        8 |       30 |        6 |     89% |67, 109, 124, 159-\>165, 163-164, 166-168 |
| src/edgebot/trading/edge.py            |       66 |        0 |       12 |        0 |    100% |           |
| src/edgebot/trading/executor.py        |       64 |        0 |        4 |        0 |    100% |           |
| src/edgebot/trading/live.py            |        4 |        0 |        0 |        0 |    100% |           |
| src/edgebot/trading/risk.py            |      106 |        0 |       26 |        0 |    100% |           |
| src/edgebot/trading/settlement.py      |       27 |        1 |        8 |        1 |     94% |        18 |
| src/edgebot/trading/sizing.py          |       18 |        1 |        4 |        1 |     91% |        29 |
| src/edgebot/trading/state.py           |       18 |        0 |        2 |        0 |    100% |           |
| **TOTAL**                              | **2659** |  **191** |  **580** |   **68** | **91%** |           |


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