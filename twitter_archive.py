import argparse
import calendar
from   collections  import defaultdict
from   datetime     import datetime
from   itertools    import groupby
from   jinja2       import Environment, FileSystemLoader, select_autoescape, pass_context
import json
from   pathlib      import Path, PurePath
import subprocess
import sys
from   urllib.parse import urlparse

def print_progress(message):
    sys.stdout.write('\u001b[2K\u001b[0E'+str(message))

def find_everything(obj,path=''):
    if isinstance(obj, dict):
        for k,v in obj.items():
            kpath = f'{path}.{k}'
            yield kpath
            yield from find_everything(v, kpath)

    if isinstance(obj, list):
        lpath = f'{path}.#'
        yield lpath
        for v in obj:
            yield from find_everything(v, lpath)

class Media:
    """
        A representation of a media item attached to a tweet - either a photo or a video.
    """
    def __init__(self, tweet, data):
        self.tweet = tweet
        self.data = data

        self.type = self.data['type']

    @property
    def url(self):
        if self.type == 'video':
            for variant in self.data['video_info']['variants']:
                name = PurePath(urlparse(variant['url']).path).name
                if (self.tweet.generator.data_root / 'tweets_media' / f'{self.tweet.id}-{name}').exists():
                    return variant['url']
        if self.type == 'photo':
            return self.data['media_url']

    @property
    def filename(self):
        url = self.url
        if url is None:
            return None

        filename = PurePath(urlparse(self.url).path).name

        return f'{self.tweet.id}-{filename}'

class Tweet:
    """
        A representation of a tweet.
    """
    def __init__(self, generator, data):
        self.generator = generator
        self.data = data
        self.created_at = datetime.strptime(self.data['created_at'], '%a %b %d %H:%M:%S %z %Y')

        self.raw_text = self.data['full_text']

        self.replies = []
        self.parent = None

        self.media = list(self.get_media())

        self.id = self.data['id_str']

    def __getitem__(self, key):
        return self.data[key]

    def get_media(self):
        for media in self.data.get('extended_entities',{}).get('media',[]):
            yield Media(self, media)

    def parents(self):
        parents = []
        t = self
        while t and t.data.get('in_reply_to_screen_name') == self.generator.my_username:
            t = t.parent
            parents.insert(0,t)

        return parents

    def thread(self):
        yield self
        for t in self.replies:
            yield from t.thread()

    @property
    def url(self):
        return PurePath('/','status',self['id_str'])
            
    @property
    def is_reply(self):
        return self.data.get('in_reply_to_screen_name')

    @property
    def is_retweet(self):
        return self.raw_text.startswith('RT ')

class TwitterArchiveGenerator:
    data_root = Path('data')
    output_root = Path('output')
    rebuild = False

    def __init__(self, data_root=Path('data'), output_root=Path('output'), rebuild=False):
        self.data_root = data_root
        self.output_root = output_root
        self.rebuild = rebuild

        self.get_my_info()
        self.get_jinja_env()
        self.convert_json()
        self.load_tweets()

    def get_my_info(self):
        with open(self.data_root / 'account.json') as f:
            d = json.load(f)[0]['account']

        self.my_username = d['username']
        self.my_info = d

    def get_jinja_env(self):
        environment = self.jinja_env = Environment(
            loader=FileSystemLoader("templates"),
            autoescape=select_autoescape()
        )


        def relative_path(target, here):
            if target.is_relative_to(here):
                return target.relative_to(here)
            else:
                for p in here.parents:
                    target = PurePath('..') / target
                return target

        @pass_context
        def relative_url(context, path):
            target = PurePath('/', path).relative_to('/')
            here = context['path'].parent.relative_to('/')
            return relative_path(target, here)

        @pass_context
        def relative_to(context, path, to):
            r = relative_path(to / path, context['path'].parent.relative_to('/'))
            return r

        @pass_context
        def tweet_content(context, tweet):
            text = tweet.raw_text
            
            reps = []

            for url in tweet.data.get('entities',{}).get('urls',[]):
                reps.append((url['indices'], f'''<a href="{url['expanded_url']}">{url['display_url']}</a>'''))

            for hashtag in tweet.data.get('entities',{}).get('hashtags',[]):
                href = relative_url(context, PurePath('/','hashtag',hashtag['text']))
                reps.append((hashtag['indices'], f'''<a class="hashtag" href="{href}">#{hashtag['text']}</a>'''))

            for mention in tweet.data.get('entities',[]).get('user_mentions',[]):
                reps.append((mention['indices'], f'''<a class="mention" href="https://twitter.com/{mention['screen_name']}">@{mention['screen_name']}</a>'''))

            for media in tweet.data.get('entities', {}).get('media', []):
                reps.append((media['indices'], ''))

            reps = [([int(i[0]),int(i[1])], rep) for i, rep in reps]

            d = 0
            for indices,rep in sorted(reps, key=lambda x:x[0][0]):
                start, end = indices
                start = int(start)
                end = int(end)
                text = text[:start+d] + rep+text[end+d:]
                d += len(rep) - (end-start)

            text = text.replace('\n', '<br>')

            return text

        def month(value):
            return calendar.month_name[value]

        def pluralise(string, n):
            return string+('' if int(n)==1 else 's')

        self.jinja_env.filters['relative_url'] = relative_url
        self.jinja_env.filters['relative_to'] = relative_to
        self.jinja_env.filters['tweet_content'] = tweet_content
        self.jinja_env.filters["month"] = month
        self.jinja_env.filters['pluralise'] = pluralise

        return self.jinja_env

    def convert_json(self):
        """
            The twitter archive contains .js files with a single JSON object that is added to a global object.
            To make these easier to read in Python, this method strips out the assignment, leaving a plain .json file with the same name.
        """
        for fname in sorted(self.data_root.iterdir()):
            if fname.suffix != '.js':
                continue
                
            json_name = fname.with_suffix('.json')

            if json_name.exists():
                continue

            with open(fname) as f:
                d = f.read()
                data = d[d.index(' = ')+3:]
                
            with open(json_name, 'w') as f:
                f.write(data)

    def load_tweets(self):
        print("Loading tweet data")

        with open(self.data_root / 'tweets.json') as f:
            self.tweets = [Tweet(self, t['tweet']) for t in json.load(f)]

        self.tweets = [t for t in self.tweets if not t.is_retweet]

        tweet_by_id = { t['id_str']:t for t in self.tweets }

        hashtags = defaultdict(list)
        hashtag_tweets = defaultdict(list)

        for t in self.tweets:
            if t.data.get('in_reply_to_screen_name') == self.my_username:
                parent = tweet_by_id.get(t.data['in_reply_to_status_id'])
                if parent:
                    parent.replies.append(t)
                    t.parent = parent

            for hashtag in t.data.get('entities',{}).get('hashtags',[]):
                tag = hashtag['text'].lower()
                hashtags[tag].append(hashtag['text'])
                hashtag_tweets[tag].append(t)

        self.hashtag_tweets = {}
        for h, variants in hashtags.items():
            canonical = min(variants)
            self.hashtag_tweets[canonical] = sorted(hashtag_tweets[h], key=lambda x: x.created_at)
    
    def render_page(self, template_name, path, extra_context, force_rebuild=False):
        template = self.jinja_env.get_template(template_name)
        output_path = self.output_root / path

        if output_path.exists() and not (self.rebuild or force_rebuild):
            return

        output_path.parent.mkdir(exist_ok=True, parents=True)

        context = {
            'my_username': self.my_username,
            'path': PurePath('/') / path,
        }
        context.update(extra_context)

        with open(output_path, 'w') as f:
            f.write(template.render(context))

    def render_single_tweet(self, tweet):
        status_dir = PurePath('status' , tweet['id_str'])
        
        self.render_page('single_tweet.html', status_dir / 'index.html', {'tweet': tweet})

        if self.rebuild:
            with open(self.output_root / 'status' / f'{tweet.id}.json', 'w') as f:
                f.write(json.dumps(tweet.data, indent=4))

    def render_single_tweets(self):
        print("Rendering individual tweets...")
        n = len(self.tweets)
        for i, tweet in enumerate(self.tweets):
            print_progress(f'{i}/{n}')
            self.render_single_tweet(tweet)
        sys.stdout.write('\n')

    def grouped_tweets(self):
        return [( year, 
                  [( month, 
                     list(month_tweets)
                   ) for month, month_tweets in groupby(year_tweets, key=lambda x: x.created_at.month)]) for year, year_tweets in groupby(sorted([t for t in self.tweets if not (t.is_retweet)], key=lambda x: x.created_at), key=lambda x: x.created_at.year)]

    def render_index(self):
        print("Rendering the index page")
        years = [(year, len(list(year_tweets)))  for year, year_tweets in groupby(sorted(self.tweets, key=lambda x: x.created_at), key=lambda x: x.created_at.year)]

        hashtags = sorted(self.hashtag_tweets.items(), key=lambda x: x[0].lower())

        self.render_page('index.html', 'index.html', {'years': years, 'hashtags': hashtags}, force_rebuild=True)

    def render_all_tweets(self):
        print("Rendering the page of every tweet")
        self.render_page('all.html', 'every_tweet.html', {'tweets': self.grouped_tweets()})

    def render_years(self):
        print("Rendering years")
        for year, year_tweets in self.grouped_tweets():
            print_progress(str(year))
            self.render_page('year.html', PurePath('year',str(year), 'index.html'), { 'tweets': year_tweets, 'year': year }, force_rebuild = True)
        sys.stdout.write('\n')

    def render_hashtags(self):
        print("Rendering hashtags")
        n = len(self.hashtag_tweets)
        for i, (hashtag, tweets) in enumerate(self.hashtag_tweets.items()):
            print_progress(f'{i}/{n} #{hashtag}')
            self.render_page('hashtag.html', PurePath('hashtag', hashtag, 'index.html'), { 'tweets': tweets, 'hashtag': hashtag }, force_rebuild = True)
        sys.stdout.write('\n')

    def copy_static(self):
        print("Copying static files")
        subprocess.run(['rsync', '-a', 'static/', self.output_root / 'static'])

    def discover_schema(self):
        """
            Work out the schema of the tweet JSON by finding all keys across all of the tweets.
        """
        paths = set()
        for t in self.tweets:
            paths = paths.union(set(find_everything(t.data)))

        for p in sorted(paths):
            bits = p.split('.')
            print('    '*len(bits[:-1])+bits[-1])

if __name__ == '__main__':
    generator = TwitterArchiveGenerator(rebuild = True)
    generator.copy_static()
    generator.render_index()
    generator.render_single_tweets()
    generator.render_all_tweets()
    generator.render_years()
    generator.render_hashtags()

    print(f"Done! The rendered archive is in:\n{generator.output_root.resolve()}")
