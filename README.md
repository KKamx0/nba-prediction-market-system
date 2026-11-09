# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/KKamx0/nba-prediction-market-system/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                   |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|--------------------------------------- | -------: | -------: | -------: | -------: | ------: | --------: |
| src/edgebot/\_\_init\_\_.py            |        1 |        0 |        0 |        0 |    100% |           |
| src/edgebot/cli.py                     |      105 |       23 |        6 |        1 |     75% |35-41, 45-49, 53-57, 86-96, 179 |
| src/edgebot/config.py                  |       57 |        1 |       10 |        2 |     96% |57, 106-\>105 |
| src/edgebot/db.py                      |       78 |        0 |        4 |        0 |    100% |           |
| src/edgebot/devig.py                   |       44 |        4 |       16 |        3 |     85% |29, 50-\>58, 67-69 |
| src/edgebot/ingest/\_\_init\_\_.py     |        0 |        0 |        0 |        0 |    100% |           |
| src/edgebot/ingest/historical\_odds.py |      103 |       18 |       34 |        8 |     75% |71-75, 80, 97, 130, 134-135, 141, 156, 173, 177-182 |
| src/edgebot/ingest/http.py             |       51 |        8 |        6 |        0 |     82% |55, 58, 65-69, 84 |
| src/edgebot/ingest/injuries.py         |      106 |       14 |       36 |        3 |     85% |69-\>66, 85, 132-138, 143-144, 147, 150-153, 160-\>163 |
| src/edgebot/ingest/nba\_stats.py       |      131 |       13 |       40 |        4 |     88% |40-47, 84, 111-112, 116-117, 240 |
| src/edgebot/ingest/odds\_api.py        |       72 |        3 |       18 |        2 |     94% |70, 75-76, 129-\>131 |
| src/edgebot/ingest/polymarket.py       |      115 |        4 |       30 |        5 |     94% |61, 80, 141, 154, 182-\>178 |
| src/edgebot/model/\_\_init\_\_.py      |        0 |        0 |        0 |        0 |    100% |           |
| src/edgebot/model/minutes.py           |      118 |        2 |       32 |        4 |     96% |105, 121-\>134, 130, 226-\>219 |
| src/edgebot/model/ratings.py           |       99 |        2 |       32 |        3 |     96% |88-\>87, 126-\>125, 139-140 |
| src/edgebot/model/strength.py          |       54 |        0 |        4 |        0 |    100% |           |
| src/edgebot/names.py                   |       14 |        0 |        2 |        0 |    100% |           |
| src/edgebot/ratelimit.py               |       26 |        0 |        4 |        0 |    100% |           |
| src/edgebot/teams.py                   |       46 |        0 |        4 |        0 |    100% |           |
| src/edgebot/timeutil.py                |       22 |        0 |        2 |        0 |    100% |           |
| **TOTAL**                              | **1242** |   **92** |  **280** |   **35** | **90%** |           |


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