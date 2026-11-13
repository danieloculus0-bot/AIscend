"""JSON-lines process exposing the actual pinned BEAN learners to the simulator.

The worker receives bounded observations and matured outcomes only. It is not
given a market-history filename, array, end-of-sample price or news backfill.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys

def main():
    learner=None;journal=None;kind=None
    for line in sys.stdin:
        try:
            r=json.loads(line);op=r["op"]
            if op=="init":
                root=Path(r["core_dir"]).resolve();sys.path.insert(0,str(root))
                from bean.optimization.autodev import Journal
                kind=r["engine"]
                if kind=="dqn":
                    import torch
                    torch.set_num_threads(1);torch.use_deterministic_algorithms(True)
                    from bean.optimization.sponge_dqn import SpongeDQN
                    journal=Journal(r["journal"])
                    if r.get("restore"):
                        learner=SpongeDQN.recover_latest(journal)
                    else:learner=SpongeDQN(seed=r["seed"],observation_size=r["size"],actions=tuple(r["actions"]),domain="aiscend.reference_portfolio.v1",batch_size=32,replay_capacity=1024,warmup=64,learn_every=1,target_sync=100,gamma=.95,lr=.0005)
                elif kind=="td":
                    from bean.learning.runtime import LearningRuntime
                    learner=LearningRuntime(r["journal"],actions=tuple(r["actions"]),seed=r["seed"])
                    learner.policy.epsilon=.15
                else:raise ValueError("Unknown actual BEAN engine")
                out={"engine":kind,"module":type(learner).__module__,"updates":learner.updates if kind=="dqn" else learner.policy.steps}
            elif op=="choose":
                if kind=="dqn":
                    action=learner.choose(r["vector"],epsilon=r.get("epsilon",0))
                else:
                    policy=learner.policy
                    state=learner.scoped("aiscend.reference_portfolio.v1",r["bins"])
                    action=policy.choose(state,explore=r.get("training",False))
                out={"action":action}
            elif op=="observe":
                if r["matured_at"]<=r["decision_at"]:raise ValueError("Reward has not matured")
                if kind=="dqn":
                    learner.observe(r["vector"],r["action"],r["reward"],r["next_vector"],r["done"])
                    for _ in range(r.get("extra_updates",3)):
                        if len(learner.replay)>=learner.warmup:learner.train_batch()
                    out={"updates":learner.updates,"frames":learner.frames,"loss":learner.last_loss}
                else:
                    policy=learner.policy
                    policy.learn(learner.scoped("aiscend.reference_portfolio.v1",r["bins"]),r["action"],r["reward"],None if r["done"] else learner.scoped("aiscend.reference_portfolio.v1",r["next_bins"]),r["done"])
                    out={"updates":policy.steps,"q_states":len(policy.q)}
            elif op=="status":
                if kind=="dqn":out={"updates":learner.updates,"frames":learner.frames,"weights_sha256":learner.weights_digest(),"replay_entries":len(learner.replay)}
                else:
                    policy=learner.policy
                    raw=json.dumps(policy.q,sort_keys=True,separators=(",",":"),allow_nan=False)
                    out={"updates":policy.steps,"q_states":len(policy.q),"weights_sha256":hashlib.sha256(raw.encode()).hexdigest()}
            elif op=="checkpoint":
                args={k:r[k] for k in ("baseline","candidate","samples","evaluation_sha256")}
                if kind=="dqn":out=learner.checkpoint(journal,episode=r["episode"],metadata=r.get("metadata"),**args)
                else:out=learner.checkpoint(**args)
            elif op=="close":
                if journal:journal.close()
                elif learner:learner.close()
                print(json.dumps({"ok":True,"result":{"closed":True}}),flush=True);return 0
            else:raise ValueError("Unsupported worker operation")
            print(json.dumps({"ok":True,"result":out},allow_nan=False),flush=True)
        except Exception as e:
            print(json.dumps({"ok":False,"error":type(e).__name__+": "+str(e)}),flush=True)
    return 0

if __name__=="__main__":raise SystemExit(main())
