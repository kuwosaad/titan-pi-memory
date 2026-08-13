import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

ARTIFACT = Path("/Users/mohammadsaad/.openclaw/night-runs/step1-step2-20260402-1512/artifacts/step1_step2_results.json")
OUT = Path("/Users/mohammadsaad/.openclaw/night-runs/step1-step2-20260402-1512/artifacts/step1_step2_comparison.png")

data = json.loads(ARTIFACT.read_text(encoding="utf-8"))

sweep = data["alpha_sweep"]
alphas = [str(entry["alpha"]) for entry in sweep]
baseline_mrrs = [entry["baseline_mrr"] * 100 for entry in sweep]
rerank_mrrs = [entry["rerank_mrr"] * 100 for entry in sweep]
baseline_hit1 = [entry["baseline_hit_at_1"] / max(entry["total_cases"], 1) * 100 for entry in sweep]
rerank_hit1 = [entry["rerank_hit_at_1"] / max(entry["total_cases"], 1) * 100 for entry in sweep]
baseline_hit3 = [entry["baseline_hit_at_3"] / max(entry["total_cases"], 1) * 100 for entry in sweep]
rerank_hit3 = [entry["rerank_hit_at_3"] / max(entry["total_cases"], 1) * 100 for entry in sweep]
pool_rates = [entry["pool_coverage"] / max(entry["total_cases"], 1) * 100 for entry in sweep]

fig, axes = plt.subplots(1, 3, figsize=(16, 6))
fig.patch.set_facecolor("#1e1e1e")
for ax in axes:
    ax.set_facecolor("#2c2c2c")
    ax.tick_params(colors="#cccccc", labelsize=9)
    ax.yaxis.label.set_color("#cccccc")
    ax.xaxis.label.set_color("#cccccc")
    for spine in ax.spines.values():
        spine.set_color("#444444")

x = range(len(alphas))
bar_width = 0.35

step1_color = "#e74c3c"
step2_color = "#27ae60"

axes[0].bar(
    [i - bar_width / 2 for i in x], baseline_mrrs,
    bar_width, color=step1_color, label="step1 only", **({"edgecolor": "#cccccc", "linewidth": 0.5})
)
axes[0].bar(
    [i + bar_width / 2 for i in x], rerank_mrrs,
    bar_width, color=step2_color, label="step1 + step2", **({"edgecolor": "#cccccc", "linewidth": 0.5})
)
axes[0].set_title("Mean Reciprocal Rank (×100)", fontsize=12, color="#ffffff", pad=10)
axes[0].set_ylabel("MRR × 100")
axes[0].set_ylim(0, 80)
axes[0].set_xticks(list(x))
axes[0].set_xticklabels([f"α={a}" for a in alphas], fontsize=9)
axes[0].legend(facecolor="#2c2c2c", edgecolor="#444444", labelcolor="#cccccc", fontsize=9)

axes[1].bar(
    [i - bar_width / 2 for i in x], baseline_hit1,
    bar_width, color=step1_color, **({"edgecolor": "#cccccc", "linewidth": 0.5})
)
axes[1].bar(
    [i + bar_width / 2 for i in x], rerank_hit1,
    bar_width, color=step2_color, **({"edgecolor": "#cccccc", "linewidth": 0.5})
)
axes[1].set_title("Hit@1 Rate (%)", fontsize=12, color="#ffffff", pad=10)
axes[1].set_ylabel("% of cases where gold is rank 1")
axes[1].set_ylim(0, 65)
axes[1].set_xticks(list(x))
axes[1].set_xticklabels([f"α={a}" for a in alphas], fontsize=9)

axes[2].bar(
    [i - bar_width / 2 for i in x], baseline_hit3,
    bar_width, color=step1_color, **({"edgecolor": "#cccccc", "linewidth": 0.5})
)
axes[2].bar(
    [i + bar_width / 2 for i in x], rerank_hit3,
    bar_width, color=step2_color, **({"edgecolor": "#cccccc", "linewidth": 0.5})
)
axes[2].set_title("Hit@3 Rate (%)", fontsize=12, color="#ffffff", pad=10)
axes[2].set_ylabel("% of cases where gold is in top 3")
axes[2].set_ylim(0, 85)
axes[2].set_xticks(list(x))
axes[2].set_xticklabels([f"α={a}" for a in alphas], fontsize=9)

step1_patch = mpatches.Patch(color=step1_color, label="step1 only (min_reliability=0.0, pool=16)")
step2_patch = mpatches.Patch(color=step2_color, label="step1 + step2 (cross-memory rerank)")
fig.legend(
    handles=[step1_patch, step2_patch],
    loc="upper center", ncol=2,
    facecolor="#2c2c2c", edgecolor="#444444", labelcolor="#cccccc", fontsize=9
)

best = data["best"]
fig.suptitle(
    f"Step1 + Step2 Combined Retrieval — {data['cases_mined']} Mined Cases\n"
    f"Best: α={best['alpha']} → MRR {best['baseline_mrr']:.3f}→{best['rerank_mrr']:.3f}  "
    f"Hit@1 {best['baseline_hit_at_1']}/{best['total_cases']}→{best['rerank_hit_at_1']}/{best['total_cases']}\n"
    f"Gold present rate: {best['pool_coverage']}/{best['total_cases']} ({best['pool_coverage']/max(best['total_cases'],1):.1%})",
    fontsize=11, color="#cccccc", y=0.98
)

plt.tight_layout(rect=[0, 0, 1, 0.88])
plt.savefig(OUT, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
print(f"Saved: {OUT}")
