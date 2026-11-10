"""Self-learning from the bot's own track record.

Everything here learns from *closing line value* and observed minutes, not
from wins and losses: a 10-5 run on one team is mostly luck, while buying
consistently below the closing price is evidence. Every pattern is shrunk
toward "no effect" and acts only when its confidence bound clears zero.

Learned rules can make the bot pickier, skip a segment, or ease the edge
requirement down to a floor. They never touch the hard risk limits, and
like everything else they are computed strictly from data known at the time.
"""
