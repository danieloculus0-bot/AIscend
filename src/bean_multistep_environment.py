"""Funded binary trade management using existing market features and account math."""
from __future__ import annotations
import copy
from decimal import Decimal, ROUND_DOWN, InvalidOperation
import hashlib
import math
import numpy as np
from .bean_economic_values import market_vector
from .bean_strategy_curriculum import Account, Candles, Features
from .bean_wide_history import DAY

def withheld(product):
    return int(hashlib.sha256(product.rsplit('-',1)[0].encode()).hexdigest(),16)%5==0

class VenueAccount(Account):
    """Reuse Account's wealth/exposure contract; enforce observed order metadata."""
    def __init__(self,cash,metadata):
        super().__init__(cash);self.initial=cash;self.metadata=metadata
        self.age=0;self.entry=None;self.rejects={}
    def trade(self,target,price,cost=.01):
        if target not in (0,1):raise ValueError('Only funded cash or long targets')
        if (target==1 and self.quantity>0) or (target==0 and self.quantity==0):return 0.
        try:
            step=Decimal(str(self.metadata['base_increment']))
            minimum=float(self.metadata.get('min_market_funds',self.metadata.get('quote_min_size')))
            if not step.is_finite() or step<=0 or not math.isfinite(minimum) or minimum<=0:raise ValueError('Invalid metadata')
        except (KeyError,TypeError,ValueError,InvalidOperation):
            self.rejects['UNKNOWN_ORDER_RULES']=self.rejects.get('UNKNOWN_ORDER_RULES',0)+1;return 0.
        amount=self.cash/(price*(1+cost)) if target else self.quantity
        quantity=float((Decimal(str(amount))/step).to_integral_value(rounding=ROUND_DOWN)*step)
        notional=quantity*price
        if notional<minimum or notional<=0:
            self.rejects['BELOW_CURRENT_MINIMUM']=self.rejects.get('BELOW_CURRENT_MINIMUM',0)+1;return 0.
        fee=notional*cost
        if target:
            self.quantity+=quantity;self.cash-=notional+fee;self.age=0;self.entry=price
        else:
            self.quantity-=quantity;self.cash+=notional-fee
            if self.quantity<1e-12:self.quantity=0.;self.age=0;self.entry=None
        if self.cash < -1e-7 or self.quantity < -1e-12:raise AssertionError('Unfunded trade')
        self.cash=max(0.,self.cash);self.costs+=fee;self.orders+=1
        return fee
    def liquidation(self,price,cost=.01):
        return self.cash+self.quantity*price*(1-cost)

def features_for(product,rows,start,end):
    times=np.arange(start,end,DAY,dtype=np.int64);arrays={k:np.full((len(times),1),np.nan) for k in ('open','high','low','close','volume')}
    for row in rows:
        i=(row['open_time']-start)//DAY
        if 0<=i<len(times):
            for key in arrays:arrays[key][i,0]=row[key]
    return Features(Candles((product,),times,arrays,'current-vintage-raw-source'))

def observation(features,i,account):
    price=float(features.c.data['close'][i,0])
    if not hasattr(features,'_wide_market_cache'):features._wide_market_cache={}
    if i not in features._wide_market_cache:features._wide_market_cache[i]=market_vector(features.vector(i,0,0.))
    base=features._wide_market_cache[i]
    extra=[account.exposure(price),min(account.age,180)/30.,0. if not account.entry else (price/account.entry-1)/.25,math.log(max(account.liquidation(price)/account.initial,1e-12))]
    return base+[float(np.clip(x,-5,5)) for x in extra]

def fixed_action(name,features,i,account):
    if name=='cash':return 0
    if name=='buy_hold':return 1
    if name=='price_trend':return int(features.c.data['close'][i,0]>features.sma[200][i,0])
    if name=='donchian_breakout':
        p=features.c.data['close'][i,0]
        return int(p>features.hi20[i,0] or (account.quantity>0 and p>=features.lo10[i,0]))
    raise ValueError('Unknown fixed control')

def valid_path(features,i,stop):
    return features.valid(i,0) and stop<len(features.c.times) and all(np.isfinite(a[i:stop+1,0]).all() for a in features.c.data.values())

def branch(features,i,account,action,*,terminal=False):
    """Seven-day transition; fill follows the completed signal by one full day."""
    if not valid_path(features,i,i+7):raise ValueError('Cannot teach across missing candles')
    before=copy.deepcopy(account);after=copy.deepcopy(account)
    p=float(features.c.data['close'][i,0]);fill=float(features.c.data['open'][i+2,0]);end=float(features.c.data['close'][i+7,0]);old=before.liquidation(p)
    paid=after.trade(action,fill)
    if after.quantity:after.age+=6 if not before.quantity else 7
    if terminal:paid+=after.trade(0,end)
    outcome=after.liquidation(end)
    reward=100*math.log(max(outcome,1e-12)/max(old,1e-12))
    before_reserve=before.quantity*p*.01;after_reserve=after.quantity*end*.01
    # Sum reconciles exactly to the change in net-liquidation wealth.
    effects={'pre_fill_market_pnl':before.quantity*(fill-p),'post_fill_market_pnl':(0. if terminal else after.quantity)*(end-fill),'paid_costs':-paid,'exit_reserve_change':before_reserve-after_reserve}
    # If the terminal close sold the position, retain its held-period PnL.
    if terminal:
        pre_exit=copy.deepcopy(before);pre_exit.trade(action,fill)
        effects['post_fill_market_pnl']=pre_exit.quantity*(end-fill)
    if abs(sum(effects.values())-(outcome-old))>1e-7:raise AssertionError('Transition reward attribution does not reconcile')
    return after,{'vector':observation(features,i,before),'action':action,'reward':reward,'next_vector':None if terminal else observation(features,i+7,after),'done':terminal,'decision_at':int(features.c.times[i])+DAY,'execution_at':int(features.c.times[i+2]),'outcome_at':int(features.c.times[i+7])+DAY,'wealth_before':old,'wealth_after':outcome,'effects':effects}

def teaching_rows(product,features,metadata,cutoff,experts):
    times=features.c.times;episode=0
    for name in experts:
        account=VenueAccount(1000.,metadata);step=0
        for i in range(200,len(times)-7,7):
            if int(times[i+7])+DAY>cutoff:break
            if not valid_path(features,i,i+7):
                account=VenueAccount(1000.,metadata);step=0;episode+=1;continue
            terminal=step==25 or i+14>=len(times) or int(times[min(i+14,len(times)-1)])+DAY>cutoff or not valid_path(features,i+7,i+14)
            for action in (0,1):
                after,row=branch(features,i,account,action,terminal=terminal)
                row.update(product=product,teacher=name,episode=episode,id=hashlib.sha256(f'{product}/{name}/{i}/{episode}/{action}'.encode()).hexdigest())
                yield row
            account,_=branch(features,i,account,fixed_action(name,features,i,account),terminal=terminal)
            step+=1
            if terminal:account=VenueAccount(1000.,metadata);step=0;episode+=1
