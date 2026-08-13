import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

ARTIFACT = Path("/Users/mohammadsaad/.openclaw/night-runs/retrieval-sweep-20260402-1132-focus/artifacts/retrieval_sweep_results.json")
OUT = Path("/Users/mohammadsaad/.openclaw/night-runs/retrieval-sweep-20260402-1132-focus/artifacts/retrieval_comparison.png")

data = json.loads(ARTIFACT.read_text(encoding="utf-8"))

variants = []
labels = []
gold_rates = []
hit3_rates = []
mrrs = []

for v in data["variants"]:
    name = v["variant"]["name"]
    m = v["metrics"]
    variants.append(v["variant"])
    labels.append(name)
    gold_rates.append(m["gold_present_rate"] * 100)
    hit3_rates.append(m["hit_at_3"] * 100)
    mrrs.append(m["mrr"] * 100)

colors = []
for label in labels:
    if label == "baseline":
        colors.append("#e74c3c")
    elif label == "low_reliability_large_pool":
        colors.append("#27ae60")
    elif label == "low_reliability_gate":
        colors.append("#2ecc71")
    else:
        colors.append("#3498db")

fig, axes = plt.subplots(1, 3, figsize=(16, 6))
fig.patch.set_facecolor("#1e1e1e")
for ax in axes:
    ax.set_facecolor("#2c2c2c")
    ax.tick_params(colors="#cccccc", labelsize=9)
    ax.yaxis.label.set_color("#cccccc")
    ax.xaxis.label.set_color("#cccccc")
    for spine in ax.spines.values():
        spine.set_color("#444444")

bar_kwargs = {"edgecolor": "#cccccc", "linewidth": 0.5}

x = range(len(labels))
bar_width = 0.55

axes[0].bar(x, gold_rates, width=bar_width, color=colors, **bar_kwargs)
axes[0].set_title("Gold Memory Present in Pool (%)", fontsize=12, color="#ffffff", pad=10)
axes[0].set_ylabel("% of cases where gold is in pool")
axes[0].set_ylim(0, 100)
axes[0].set_xticks(list(x))
axes[0].set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
axes[0].axhline(y=gold_rates[labels.index("baseline")], color="#e74c3c", linestyle="--", linewidth=1, alpha=0.6)

axes[1].bar(x, hit3_rates, width=bar_width, color=colors, **bar_kwargs)
axes[1].set_title("Hit@3 Rate (%)", fontsize=12, color="#ffffff", pad=10)
axes[1].set_ylabel("% of cases where gold is in top 3")
axes[1].set_ylim(0, 60)
axes[1].set_xticks(list(x))
axes[1].set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
axes[1].axhline(y=hit3_rates[labels.index("baseline")], color="#e74c3c", linestyle="--", linewidth=1, alpha=0.6)

axes[2].bar(x, mrrs, width=bar_width, color=colors, **bar_kwargs)
axes[2].set_title("Mean Reciprocal Rank (%)", fontsize=12, color="#ffffff", pad=10)
axes[2].set_ylabel("MRR × 100")
axes[2].set_ylim(0, 35)
axes[2].set_xticks(list(x))
axes[2].set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
axes[2].axhline(y=mrrs[labels.index("baseline")], color="#e74c3c", linestyle="--", linewidth=1, alpha=0.6)

baseline_patch = mpatches.Patch(color="#e74c3c", label="baseline")
best_patch = mpatches.Patch(color="#27ae60", label="best variant")
other_patch = mpatches.Patch(color="#3498db", label="other variant")
fig.legend(handles=[baseline_patch, best_patch, other_patch], loc="upper center", ncol=3,
           facecolor="#2c2c2c", edgecolor="#444444", labelcolor="#cccccc", fontsize=9)

fig.suptitle(f"Retrieval Settings Sweep — {data['cases_mined']} Mined Cases\n"
             f"Source: {data['live_db']}",
             fontsize=11, color="#cccccc", y=0.98)

plt.tight_layout(rect=[0, 0, 1, 0.91])
OUT.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(OUT, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
print(f"Saved: {OUT}")
