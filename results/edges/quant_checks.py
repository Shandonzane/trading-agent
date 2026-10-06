import numpy as np, pandas as pd, json
px=pd.read_csv('data/cache/macro/extra_prices.csv',index_col=0,parse_dates=True)
vix=pd.read_csv('data/cache/macro/sector_prices.csv',index_col=0,parse_dates=True)['^VIX']
import yfinance as yf
v=yf.download('^VIX',start='1990-01-01',progress=False,auto_adjust=False)['Close']; v=v.iloc[:,0] if isinstance(v,pd.DataFrame) else v; v.index=v.index.tz_localize(None)
def m(r,name=''):
    r=r.dropna(); eq=(1+r).cumprod(); yrs=len(r)/252
    return dict(sharpe=round(r.mean()/r.std()*np.sqrt(252),2), cagr=round(eq.iloc[-1]**(1/yrs)-1,4), mdd=round((eq/eq.cummax()-1).min(),3), years=round(yrs,1))
out={}
# 1 PUT index vs SPY
r=px[['^PUT','SPY']].pct_change().dropna()
out['put_write']={}
for lab,sel in (('1996-2012',r.index.year<=2012),('2013-2026',r.index.year>2012),('all',slice(None))):
    rr=r[sel] if not isinstance(sel,slice) else r
    out['put_write'][lab]={'PUT':m(rr['^PUT']),'SPY':m(rr['SPY'])}
# 2 crash dip sleeve: 80% SPY + 20% cash; deploy cash into SPY when VIX>thr, back to cash when VIX<exit
spy=px['SPY'].pct_change().dropna(); vv=v.reindex(spy.index).ffill().shift(1)
out['vix_dip_sleeve']={}
for thr,ex in ((30,20),(35,20),(25,18)):
    on=np.zeros(len(spy),bool); st=False
    for i,(a) in enumerate(vv.to_numpy()):
        if not st and a>thr: st=True
        elif st and a<ex: st=False
        on[i]=st
    w=np.where(on,1.0,0.8)
    strat=spy*w - 0.0005*np.abs(np.diff(w,prepend=0.8))
    base80=spy*0.8
    out['vix_dip_sleeve'][f'in>{thr},out<{ex}']={'sleeve':m(strat),'spy80':m(base80),'spy100':m(spy),'episodes':int(((on[1:])&(~on[:-1])).sum()),'days_on':int(on.sum())}
# also drawdown-based: deploy when SPY 10%/15%/20% below 1y high
hi=px['SPY'].rolling(252).max(); dd=(px['SPY']/hi-1).shift(1).reindex(spy.index)
for d in (0.1,0.15,0.2):
    on=np.zeros(len(spy),bool); st=False
    for i,a in enumerate(dd.to_numpy()):
        if not st and a<-d: st=True
        elif st and a>-d/2: st=False
        on[i]=st
    w=np.where(on,1.0,0.8); strat=spy*w-0.0005*np.abs(np.diff(w,prepend=0.8))
    out['vix_dip_sleeve'][f'drawdown>{int(d*100)}%']={'sleeve':m(strat),'spy80':m(spy*0.8),'episodes':int(((on[1:])&(~on[:-1])).sum()),'days_on':int(on.sum())}
# 3 trend overlay: SPY when above 200 SMA at prior close, else TLT; since 2003
s=px['SPY']; t=px['TLT']; sig=(s>s.rolling(200).mean()).shift(1)
r=pd.DataFrame({'spy':s.pct_change(),'tlt':t.pct_change()}).dropna()
sig=sig.reindex(r.index)
strat=np.where(sig,r.spy,r.tlt)-0.0005*np.abs(sig.astype(float).diff().fillna(0))
strat=pd.Series(strat,index=r.index)
out['trend_200_spy_tlt']={lab:{'strategy':m(strat[sel]),'spy':m(r.spy[sel])} for lab,sel in (('2003-2012',r.index.year<=2012),('2013-2026',r.index.year>2012))}
# 4 dual momentum monthly: universe SPY,EWJ,EWG,EEM,FXI,EWY,EWZ,GLD,TLT; pick top-1 and top-3 by 12m return minus 1m; if best 12m <0 hold TLT
uni=['SPY','EWJ','EWG','EEM','FXI','EWY','EWZ','GLD','TLT']
mp=px[uni].resample('ME').last().dropna()
mom=mp.pct_change(12)
mr=mp.pct_change()
res={}
for k in (1,3):
    rets=[]
    for i in range(13,len(mp)):
        sc=mom.iloc[i-1].drop('TLT')
        top=sc.sort_values(ascending=False).index[:k]
        pick=[x for x in top if sc[x]>0] or ['TLT']
        rets.append(mr.iloc[i][pick].mean()-0.001)
    rr=pd.Series(rets,index=mp.index[13:])
    def mm(r):
        eq=(1+r).cumprod(); yrs=len(r)/12
        return dict(sharpe=round(r.mean()/r.std()*np.sqrt(12),2),cagr=round(eq.iloc[-1]**(1/yrs)-1,4),mdd=round((eq/eq.cummax()-1).min(),3))
    res[f'top{k}']={lab:{'strategy':mm(rr[sel]),'spy':mm(mr['SPY'].reindex(rr.index)[sel])} for lab,sel in (('2005-2012',rr.index.year<=2012),('2013-2026',rr.index.year>2012))}
out['dual_momentum']=res
# 5 big-day continuation on meme names: after a +20% day, next 1/5/20 day returns
rows={}
for sym in ['GME','AMC','TSLA','NVDA']:
    p=px[sym] if sym in px else pd.read_csv(f'data/cache/{sym}.csv',index_col=0,parse_dates=True)['close']
    r=p.pct_change(); big=r[r>0.2].index
    f={}
    for h in (1,5,20):
        fwd=[(p.shift(-h)/p-1)[d] for d in big if d in p.index]
        fwd=[x for x in fwd if pd.notna(x)]
        f[f'{h}d']={'n':len(fwd),'mean':round(float(np.mean(fwd)),3) if fwd else None,'median':round(float(np.median(fwd)),3) if fwd else None,'win':round(float(np.mean(np.array(fwd)>0)),2) if fwd else None}
    rows[sym]=f
out['after_plus20pct_day']=rows
json.dump(out,open('results/edges/quant_checks.json','w'),indent=1,default=float)
print(json.dumps(out,indent=1,default=float))
