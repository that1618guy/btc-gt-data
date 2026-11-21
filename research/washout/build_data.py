"""Assemble one aligned daily panel for the Washout research.
Sources (all public): Cboe history files (VIX, VVIX, VIX3M, COR1M), Yahoo ^GSPC / SPY / BTC-USD bars,
mirrored in public GitHub data repos; COR1M Oct 28 - Nov 19 2026 taken from Cboe's own CSV.
Nov 20 2026 is provisional: the 3:57pm ET values from the reference screenshot."""
import sys, pandas as pd, numpy as np
RAW = sys.argv[1]; OUT = sys.argv[2]

def cboe(path, datecol='date', fmt=None):
    d = pd.read_csv(path)
    d.columns = [c.lower() for c in d.columns]
    d['date'] = pd.to_datetime(d['date'], format=fmt)
    col = 'close' if 'close' in d.columns else [c for c in d.columns if c != 'date'][0]
    return d.set_index('date')[col].astype(float).sort_index()

spx  = cboe(f'{RAW}/os/IDX_GSPC.csv')
spy  = cboe(f'{RAW}/os/SPY.csv')
vix  = cboe(f'{RAW}/os/cboe_VIX.csv')
vvix = cboe(f'{RAW}/os/cboe_VVIX.csv')
vix3m= cboe(f'{RAW}/os/cboe_VIX3M.csv')
btc  = cboe(f'{RAW}/os/BTC-USD.csv')
c1   = cboe(f'{RAW}/disp/cboe_COR1M.csv', fmt='%m/%d/%Y')
c1t  = cboe(f'{OUT}/cor1m_cboe_tail.csv', fmt='%m/%d/%Y')
cor1m = pd.concat([c1, c1t[~c1t.index.isin(c1.index)]]).sort_index()
cor1m = cor1m[~cor1m.index.duplicated(keep='last')]

# trading calendar = SPX sessions; drop the partial Nov 20 bars, rebuild that day from the screenshot
end_full = pd.Timestamp('2026-11-19')
cal = spx.index[(spx.index >= '1990-01-02') & (spx.index <= end_full)]
P = pd.DataFrame(index=cal)
P['spx'] = spx.reindex(cal); P['spy'] = spy.reindex(cal)
P['vix'] = vix.reindex(cal); P['vvix'] = vvix.reindex(cal)
P['vix3m'] = vix3m.reindex(cal); P['cor1m'] = cor1m.reindex(cal)

d20 = pd.Timestamp('2026-11-20')
spy_prev = P.loc[end_full, 'spy']; spx_prev = P.loc[end_full, 'spx']
P.loc[d20, ['spy','vix','vvix','cor1m']] = [687.32, 40.03, 146.36, 44.71]
P.loc[d20, 'spx'] = spx_prev * 687.32 / spy_prev
P['provisional'] = 0; P.loc[d20, 'provisional'] = 1
P = P.sort_index(); P.index.name = 'date'

# fill single missing prints inside a series' live range (holiday-calendar mismatches), never across starts
for c in ['vix','vvix','vix3m','cor1m','spy']:
    first = P[c].first_valid_index()
    gaps = P.loc[first:, c].isna().sum()
    P.loc[first:, c] = P.loc[first:, c].ffill(limit=2)
    print(f'{c:6s} from {first.date()}  gaps filled: {gaps}')
P.to_csv(f'{OUT}/panel.csv', float_format='%.4f')

b = btc[btc.index <= d20].copy(); b.index.name='date'
b.to_frame('btc').to_csv(f'{OUT}/btc.csv', float_format='%.2f')
print(P.tail(4).to_string()); print('rows', len(P), 'btc rows', len(b), b.index[0].date(), b.index[-1].date())
