# CLP's Twitter archive generator

This Python script takes the archive you obtain from Twitter and produces a nice static site containing all the tweets, in a form suitable for publishing online.

Install the required packages with `pip install -r requirements.txt`.

Extract the `.zip` file you get from Twitter into the same directory as this script.

Run `python twitter_archive.py`.

The output is in a directory called `output`. Copy the `data/tweets_media` folder from your Twitter archive to `output/media`.

I just made this for my own Twitter archive, and I'm not hugely interested in maintaining a generic tool for everybody. If it's useful to you as it is, then that's lovely!
