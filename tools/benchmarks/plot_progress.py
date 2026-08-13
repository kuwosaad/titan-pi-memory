import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

ARTIFACT = Path("/Users/mohammadsaad/.openclaw/night-runs/retrieval-sweep-20260402-1132-focus/artifacts/retrieval_sweep_results.json")
OUT = Path("/Users/mohammadsaad/Desktop/autoresearch_progress.png")

data = json.loads(ARTIFACT.read_text(encoding="utf-8"))

variants = []
labels = []
mrrs = []
gold_rates = []
hit3s = []

for v in data["variants"]:
    name = v["variant"]["name"]
    m = v["metrics"]
    variants.append(v["variant"])
    labels.append(name)
    mrrs.append(m["mrr"] * 100)
    gold_rates.append(m["gold_present_rate"] * 100)
    hit3s.append(m["hit_at_3"] * 100)

expts = list(range(1, len(variants) + 1))

fig, ax = plt.subplots(figsize=(10, 6))
fig.patch.set_facecolor("#111827")
ax.set_facecolor("#1f2937")
ax.tick_params(colors="#d1d5db", labelsize=11)
ax.yaxis.label.set_color("#d1d5db")
ax.xaxis.label.set_color("#d1d5db")
ax.title.set_color("#ffffff")
for spine in ax.spines.values():
    spine.set_color("#374151")

best_mrr = -1
kept_x_mrr = []
kept_y_mrr = []
kept_labels_mrr = []
discarded_x_mrr = []
discarded_y_mrr = []

for i, (x, y, label) in enumerate(zip(expts, mrrs, labels), start=1):
    if y > best_mrr:
        best_mrr = y
        kept_x_mrr.append(x)
        kept_y_mrr.append(y)
        kept_labels_mrr.append(label)
    else:
        discarded_x_mrr.append(x)
        discarded_y_mrr.append(y)

ax.scatter(discarded_x_mrr, discarded_y_mrr, color="#6b7280", s=70, zorder=3, label="discarded")
ax.scatter(kept_x_mrr, kept_y_mrr, color="#22c55e", s=100, zorder=4, linewidths=1.5, edgecolors="#ffffff")
ax.plot(kept_x_mrr, kept_y_mrr, color="#22c55e", linewidth=2, zorder=3, alpha=0.9)

for xi, yi, lbl in zip(kept_x_mrr, kept_y_mrr, kept_labels_mrr):
    ax.annotate(lbl, (xi, yi), textcoords="offset points",
                xytext=(7, 6), color="#22c55e", fontsize=9,
                arrowprops=dict(arrowstyle="->", color="#22c55e", lw=1.2))

ax.set_xlabel("Experiment #", fontsize=12)
ax.set_ylabel("Mean Reciprocal Rank (×100)", fontsize=12)
ax.set_xticks(expts)
ax.set_xticklabels([str(e) for e in expts])
ax.set_ylim(0, max(mrrs) * 1.15)
ax.set_xlim(0.5, len(expts) + 0.5)
ax.grid(True, color="#374151", linewidth=0.5, alpha=0.5)

ax.set_title(
    f"Autoresearch Progress: {len(variants)} Experiments, {len(kept_y_mrr)} Kept Improvements\n"
    f"Metric: MRR ×100  |  Best: {max(mrrs):.1f}  |  Source: retrieval_sweep_20260402-1132-focus",
    fontsize=11, pad=12
)

plt.tight_layout()
plt.savefig(OUT, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
print(f"Saved: {OUT}")
