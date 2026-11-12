import json

# Read GovTribe grants
with open('research/bcc_analysis/govtribe_grants.json') as f:
    govtribe = json.load(f)
print('GovTribe grants:', len(govtribe.get('grants', [])))
for g in govtribe.get('grants', []):
    print('  {}: ${} {} {}'.format(g['award_id'], g['amount'], g['program'], g['date_awarded']))

# Read news articles
with open('research/bcc_analysis/news_articles.json') as f:
    news = json.load(f)
print('\nNews articles:', len(news.get('articles', [])))
for a in news.get('articles', []):
    print('  {} ({}): {}'.format(a['title'][:60], a['date'], a['url'][:80]))