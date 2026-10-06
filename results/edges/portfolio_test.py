"""Split-core + thematic-sleeve portfolios vs plain SPY, 2006-06 to 2026-10, quarterly rebalance, 5 bp per trade.
Sleeve 'pair' = long SPY / short IGV (software), i.e. the "short the losers of AI" idea, held as a pair.
Intl = monthly dual momentum between EFA, EEM and EWJ (hold the best 12-month performer; SHY if all negative)."""
import json, numpy as np, pandas as pd
px=pd.read_csv('data/cache/macro/theme_prices.csv',index_col=0,parse_dates=True).loc['2006-06-01':]
px=px.drop(columns=['URA']).dropna()
r=px.pct_change().fillna(0)
# intl dual momentum sleeve as a daily return series
mp=px[['EFA','EEM','EWJ','SHY']].resample('ME').last(); mom=mp.pct_change(12)
pick=pd.Series(index=px.index,dtype=object)
for i in range(13,len(mp)):
    sc=mom.iloc[i-1][['EFA','EEM','EWJ']]; best=sc.idxmax(); pick.loc[mp.index[i-1]+pd.Timedelta(days=1):mp.index[i]]=best if sc[best]>0 else 'SHY'
pick=pick.ffill().bfill()
r['INTL']=np.array([r.loc[d,p] for d,p in zip(px.index,pick)])
r['PAIR']=r['SPY']-r['IGV']           # long SPY, short software (zero net exposure)
r['CASH']=r['SHY']
above=(px['SPY']>px['SPY'].rolling(200).mean()).shift(1).fillna(True)
r['SPYTREND']=np.where(above,r['SPY'],r['SHY'])-0.0005*np.abs(above.astype(float).diff().fillna(0))
P={
 'SPY only':        {'SPY':1.0},
 'Balanced split':  {'SPY':.40,'RSP':.15,'INTL':.15,'^PUT':.15,'XLU':.025,'XLE':.025,'ITB':.025,'GLD':.025,'PAIR':.025,'CASH':.025},
 'Speculative lean':{'SPY':.25,'RSP':.10,'INTL':.15,'^PUT':.10,'XLU':.08,'XLE':.07,'ITB':.06,'GLD':.09,'PAIR':.05,'TLT':.05},
 'Speculative + trend':{'SPYTREND':.25,'RSP':.10,'INTL':.15,'^PUT':.10,'XLU':.08,'XLE':.07,'ITB':.06,'GLD':.09,'PAIR':.05,'TLT':.05},
 'Themes only':     {'XLU':.25,'XLE':.2,'ITB':.15,'GLD':.25,'PAIR':.15},
}
def run(w):
    cols=list(w); W=np.array([w[c] for c in cols]); R=r[cols].to_numpy()
    cur=W.copy(); eq=np.empty(len(R)); v=1.0; q=px.index.to_period('Q')
    for i in range(len(R)):
        g=cur*(1+R[i]); v*=g.sum(); cur=g/g.sum()
        if i+1<len(R) and q[i+1]!=q[i]:
            v*=1-0.0005*np.abs(cur-W).sum(); cur=W.copy()
        eq[i]=v
    return pd.Series(eq,index=px.index)
def m(e):
    rr=e.pct_change().dropna(); yrs=len(rr)/252
    return {'cagr':round(float(e.iloc[-1]**(1/yrs)-1),4),'sharpe':round(float(rr.mean()/rr.std()*np.sqrt(252)),2),'mdd':round(float((e/e.cummax()-1).min()),3)}
def sub(e,a,b):
    s=e.loc[a:b]; return round(float(s.iloc[-1]/s.iloc[0]-1),3)
eqs={k:run(w) for k,w in P.items()}
periods={'2007-10 to 2009-03 (GFC)':('2007-10-09','2009-03-09'),'2009-03 to 2019-12 (bull)':('2009-03-09','2019-12-31'),
 '2020-02 to 2020-03 (COVID)':('2020-02-19','2020-03-23'),'2022 (rate shock)':('2022-01-03','2022-12-30'),
 '2023-01 to 2025-12 (AI boom)':('2023-01-03','2025-12-31'),'2026 YTD':('2026-01-02','2026-10-05')}
out={}
for k,e in eqs.items():
    out[k]={'full':m(e), **{p:sub(e,a,b) for p,(a,b) in periods.items()}}
    yrs=e.resample('YE').last().pct_change().dropna(); out[k]['years']={str(y.year):round(float(v),3) for y,v in yrs.items()}
    out[k]['first_year_partial']=round(float(e.resample('YE').last().iloc[0]-1),3)
# individual sleeves for reference
for c in ['SPYTREND','RSP','INTL','^PUT','XLU','XLE','ITB','GLD','PAIR','TLT','IGV']:
    e=(1+r[c]).cumprod(); out['sleeve '+c]={'full':m(e),**{p:sub(e,a,b) for p,(a,b) in periods.items()}}
json.dump(out,open('results/edges/portfolio_test.json','w'),indent=1)
rows=[]
for k,v in out.items():
    rows.append([k,v['full']['cagr'],v['full']['sharpe'],v['full']['mdd']]+[v[p] for p in periods])
df=pd.DataFrame(rows,columns=['portfolio','cagr','sharpe','mdd']+list(periods))
pd.set_option('display.width',250); print(df.to_string(index=False))
print(pd.DataFrame({k:out[k]['years'] for k in P}).to_string())
