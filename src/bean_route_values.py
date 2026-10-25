"""Net-liquidation scenario values reserve the cost of closing held inventory."""
from __future__ import annotations
import math
from .bean_strategy_curriculum import ACTIONS,Account,COST

def liquidation_values(vector,forecast,cost=COST):
    if len(vector)!=47 or len(forecast)!=2:raise ValueError('Unregistered market/account observation')
    if not all(math.isfinite(x) and abs(x)<500 for x in forecast):raise ValueError('Invalid forecast')
    exposure=vector[15]
    if not 0<=exposure<=1 or not 0<=cost<1:raise ValueError('Invalid funded position or costs')
    fill=math.exp(forecast[0]/100);end=fill*math.exp(forecast[1]/100)
    initial_liquidation=1-exposure*cost;rows=[]
    for a,target in enumerate(vector[36:]):
        account=Account(1-exposure,exposure);account.trade(None if a==1 else float(target),fill,cost)
        pending_exit=account.quantity*end*cost
        final_liquidation=account.value(end)-pending_exit
        rows.append({'action':ACTIONS[a],'net_log_return':100*math.log(final_liquidation/initial_liquidation),'net_liquidation_return':final_liquidation/initial_liquidation-1,'paid_cost':account.costs,'pending_exit_cost':pending_exit,'initial_liquidation_equity':initial_liquidation,'final_liquidation_equity':final_liquidation})
    return rows

class LiquidationAdapter:
    """Compose the exit reserve with actual future Core market forecasts."""
    def __init__(self,market_worker,cost=COST):self.worker=market_worker;self.cost=cost
    def call(self,op,**kw):
        result=self.worker.call(op,**kw)
        if op=='status':return {**result,'valuation':'net_liquidation'}
        if op!='values':return result
        if 'market_forecasts' not in result:raise ValueError('A market forecast model is required')
        components=[liquidation_values(v,f,self.cost) for v,f in zip(kw['vectors'],result['market_forecasts'])]
        return {**result,'values':[[x['net_log_return'] for x in rows] for rows in components],'valuation_components':components,'valuation':'net_liquidation'}
