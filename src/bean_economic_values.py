"""Economic action values: learn market movement, calculate funded trades exactly.

These functions do not forecast skill or profit. They prevent separately learned
action names from assigning different outcomes to the same economic exposure.
"""
from __future__ import annotations
import math
from .bean_strategy_curriculum import ACTIONS,Account,COST

def market_vector(vector):
    if len(vector)!=47:raise ValueError('Expected registered 47-value candle observation')
    # Exclude current portfolio exposure and all action-dependent target weights.
    return list(vector[:15])+list(vector[16:36])

def funded_values(vector,forecast,cost=COST):
    if len(vector)!=47:raise ValueError('Expected registered candle observation')
    if len(forecast)!=2 or not all(math.isfinite(x) and abs(x)<500 for x in forecast):raise ValueError('Invalid gap/holding return forecast')
    if not 0<=vector[15]<=1:raise ValueError('Invalid funded exposure')
    gap,holding=[math.exp(x/100) for x in forecast];exposure=vector[15];rows=[]
    for a,target in enumerate(vector[36:]):
        account=Account(1-exposure,exposure)
        account.trade(None if a==1 else float(target),gap,cost)
        after=account.value(gap*holding)
        rows.append({'action':ACTIONS[a],'net_log_return':100*math.log(after),'net_return':after-1,'paid_cost':account.costs,'predicted_market_pnl':after-1+account.costs})
    return rows

def choose(values,*,mentor='hold',minimum_increment=.10):
    """Require a predeclared incremental modeled gain over the reference action."""
    if len(values)!=len(ACTIONS) or not all(math.isfinite(x) for x in values):raise ValueError('Invalid strategy values')
    reference=ACTIONS.index(mentor);best=max(range(len(values)),key=lambda i:values[i])
    return best if values[best]>values[reference]+minimum_increment else reference

def gate_overrides(fold_advantages,forecast_better_than_mean):
    return len(fold_advantages)==2 and len(forecast_better_than_mean)==2 and all(x>0 for x in fold_advantages) and all(forecast_better_than_mean)
