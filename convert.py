import pandas as pd
import json

df = pd.read_csv('rn_jobs_with_incentives.csv', encoding='utf-8')
json_data = df.to_json('jobs.json', orient='records', lines=False, indent=2)
