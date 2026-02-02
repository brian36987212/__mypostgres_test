import pandas as pd
import glob

files = glob.glob('stocks_category/*.csv')
total = 0

for f in files:
    count = len(pd.read_csv(f))
    total += count
    print(f'{f}: {count} 支')

print(f'\n總計: {total} 支')
