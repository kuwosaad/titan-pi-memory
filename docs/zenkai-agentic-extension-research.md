# Zenkai and narrow agentic extensions: research note

**Checked:** 2026-08-29
**Question:** What does the primary literature support about making each Pi extension a narrow embedded agent, while keeping Zenkai as an external improvement agent that studies and improves another harness rather than directly self-modifying?

## Short answer

The proposed separation is well motivated, but the strongest claim should be modest:

- **Supported:** LLM agents can improve at test time from feedback, reflection, memory, search, and tool/workflow changes. Gains are most credible when the feedback channel is external, executable, or otherwise independently checkable.
- **Supported:** An external improver can change a target agent or scaffold while leaving the target’s base model and runtime stable. This is the pattern in critique-assisted oversight, meta-agent work, STOP, and the fixed-meta-agent baselines discussed by DGM.
- **Supported:** Code-level evolutionary systems can find useful agent/tool/workflow changes when every candidate is sandboxed and benchmark-scored. DGM and AlphaEvolve are demonstrations of this bounded form of improvement, not evidence that unrestricted recursive self-improvement is safe or open-ended.
- **Not established:** A model judging its own outputs is a reliable improvement signal. Self-rewarding systems report benchmark gains, but self-preference, verbosity/position bias, reward hacking, and error accumulation create a circularity problem.

The practical implication for Titan/Pi is not to redefine every extension as an agent. An extension is an integration boundary; it may connect to a dedicated Pi-powered specialist agent when the job requires interpretation. Zenkai should be such a separate agent, with its own `AGENTS.md`, model policy, context, tools, and run identity. The extension loaded into Ayanokoji should be only the deterministic bridge, UI, scheduler, and guarded apply/rollback controller. Zenkai should propose versioned changes from outside Ayanokoji's execution context and should not have implicit authority to rewrite the live harness, identity/constitution, evaluator, or safety boundary.

## What the literature actually demonstrates

### Reflection and iterative refinement

- **Reflexion** converts task feedback into verbal reflections stored in an episodic memory buffer. It does not update model weights, yet reports gains across sequential decision-making, coding, and language reasoning; its HumanEval experiment reports 91% pass@1 for the tested setup. The result supports a Pi extension that records actionable failure summaries and reuses them on later trials, but it does not show that free-form reflection is truthful or that it generalizes beyond the measured tasks. ([Shinn et al., 2023](https://arxiv.org/abs/2303.11366))
- **Self-Refine** uses one LLM as generator, feedback provider, and refiner, iterating until a task-specific stop condition. Across seven tasks it reports roughly 20 percentage points of average improvement. This establishes that a narrow embedded feedback loop can be useful without training, but it also makes the generator and evaluator highly correlated. ([Madaan et al., 2023](https://arxiv.org/abs/2303.17651), §§2–3)
- OpenAI’s **CriticGPT** study is a useful counterpoint: a separately trained critic helped people find more bugs in ChatGPT code, while model critiques had more hallucinated problems and nitpicks than human critiques. The paper explicitly reports a precision/recall tradeoff and warns that critics can introduce new systematic biases into downstream RLHF labels. Human–critic teams performed better on the tested review task than either model-only approach. ([Saunders et al., 2024](https://openai.com/index/finding-gpt4s-mistakes-with-gpt-4/); [paper](https://cdn.openai.com/llm-critics-help-catch-llm-bugs-paper.pdf))
- **Constitutional AI** uses written principles to elicit self-critiques and revisions, then trains a preference model from AI comparisons for RLAIF. It shows that explicit principles and AI feedback can reduce dependence on human harmlessness labels, but the judging process remains a learned proxy and the constitution is not a proof of alignment. ([Bai et al., 2022](https://arxiv.org/abs/2212.08073))

### Persistent personal-agent improvement

- **PAST-Bench** evaluates whether personal agents actually improve across fresh sessions by turning retained experience on and off under matched conditions. Across seven models and four frameworks, retained experience produced real but uneven gains. Crucially, agents with similar headline gains differed in whether traces showed the intended pathway: saving, retrieving, applying, and updating the relevant state. This directly supports Zenkai's provenance and application-outcome ledger; it should measure the complete `memory -> proposal -> retrieval/use -> observed outcome` pathway, not merely proposal counts or LLM grades. ([Xue et al., 2026](https://arxiv.org/abs/2608.04003))

### Self-reward and evaluator circularity

- **Self-Rewarding Language Models** use the same model as an instruction follower and LLM-as-a-Judge, then iterate DPO. The authors report improvements in both instruction following and self-rewarding ability, with a Llama 2 70B experiment improving on AlpacaEval 2.0. This is evidence that self-generated preference data can move a benchmark, not evidence that the model’s notion of quality is becoming more correct. ([Yuan et al., 2024](https://arxiv.org/abs/2401.10020))
- **Meta-Rewarding** adds a meta-judge signal to train the model’s judging ability and reports higher win rates on AlpacaEval 2 and Arena-Hard. Again, the evaluation and optimization objectives are related to the same model family and benchmark ecosystem, so the result should be read as capability improvement under a proxy, not an independent safety validation. ([Wu et al., 2024](https://arxiv.org/abs/2407.19594))
- **Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena** documents position, verbosity, and self-enhancement biases and proposes mitigations. **Self-Preference Bias in LLM-as-a-Judge** reports that GPT-4 can favor lower-perplexity/familiar text, including when it is not the judge’s own output. Together these results directly argue against letting a single Pi/Zenkai model be the sole judge of its own proposed changes. ([Zheng et al., 2023](https://arxiv.org/abs/2306.05685); [Wataoka et al., 2024](https://arxiv.org/abs/2410.21819))

### Solver–verifier separation

- **Sol-Ver** jointly trains one model to generate code and tests, reporting improvements on MBPP and LiveCodeBench. Its introduction states the central failure mode plainly: naive self-training on model outputs can accumulate errors and collapse generalization. The result supports executable tests as part of a narrow extension contract, while the error-accumulation warning argues for hidden or external checks. ([Lin et al., 2025](https://arxiv.org/abs/2502.14948))
- A theoretical/empirical study models self-improvement through a **solver–verifier gap**: improvement is possible when verification is meaningfully different from generation, and the gap narrows as the solver improves. This is a useful design hypothesis, not a general theorem that any external verifier is sound. ([Sun et al., 2025](https://arxiv.org/abs/2507.00075))

### Automated program and harness improvement

- **Automated Design of Agentic Systems (ADAS)** frames agent design as a search problem with three distinct pieces: a representable agent search space, a meta-agent/search algorithm, and an evaluation function. Its Meta Agent Search iteratively programs new agents from an archive. This is direct support for treating Zenkai as an improver of agent scaffolds rather than merging it into the target agent's ordinary task loop. ([Hu et al., 2024](https://arxiv.org/abs/2408.08435))
- **STOP (Self-Taught Optimizer)** recursively improves a scaffolding program that queries a fixed GPT-4. It reports better downstream program generation and explores beam search, genetic algorithms, and simulated annealing. The authors explicitly say this is **not full recursive self-improvement because the language model is not altered**, and they evaluate how often generated code bypasses a sandbox. This is close to the proposed “Zenkai improves a harness” boundary. ([Zelikman et al., 2024](https://arxiv.org/abs/2310.02304))
- **A Self-Improving Coding Agent (SICA)** lets a coding agent edit and re-benchmark its own source, targeting cost, speed, and benchmark performance. Its workshop paper and author repository are direct evidence that self-editing agent code is feasible, but the result is a small, benchmarked coding experiment, not a safety case for unrestricted live self-modification. ([Robeyns et al., 2025](https://arxiv.org/abs/2504.15228); [author repository](https://github.com/MaximeRobeyns/self_improving_coding_agent))
- **Darwin Gödel Machine (DGM)** maintains an archive of agent versions, samples parents, proposes codebase changes, and retains only candidates that compile, retain code-editing ability, and score on coding benchmarks. In the reported experiments, performance rose from 20.0% to 50.0% on SWE-bench and from 14.2% to 30.7% on full Polyglot. The paper compares against a fixed meta-agent and a latest-version-only hill-climber; the archive avoids losing useful stepping stones and allows recovery from performance dips. The authors also state that the experiments used sandboxing and human oversight, and that the archive/parent-selection mechanism itself remained fixed. ([Zhang et al., 2025/2026](https://arxiv.org/abs/2505.22954), §§3–4)
- **AlphaEvolve** combines LLM-generated code with automated evaluators and evolutionary selection. Its white paper reports algorithmic and infrastructure results in domains with objective, executable scoring. This is strong evidence for an external evolutionary loop where the evaluator is part of the task harness; it is weaker evidence for open-ended improvement on subjective agent behavior. ([Novikov et al., 2025](https://arxiv.org/abs/2506.13131); [official research page](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/))
- **SEAL (Self-Adapting Language Models)** goes further by having a model generate finetuning data and update directives for persistent weight updates, trained with downstream performance as reward. It is evidence that model-directed persistent adaptation is experimentally possible, but it also illustrates why weight updates should be a later, separately governed phase for Zenkai: rollback, contamination, and evaluator validity are harder than for versioned harness patches. ([Zweiger et al., 2025](https://arxiv.org/abs/2506.10943); [author repository](https://github.com/Continual-Intelligence/SEAL))

## Design implications for Pi and Zenkai

### 1. Make agent support optional, not the definition of an extension

Official Pi separates extensions from agent sessions. Extensions register tools, commands, UI, and lifecycle handlers; the SDK separately exposes `createAgentSession()`, in-memory sessions, custom system prompts, and explicit tool selection. This makes an agent-backed extension natural without making every extension autonomous. Pi also states that it has no built-in permission sandbox, so an embedded session's safety must come from bounded custom tools or process isolation rather than prompt instructions. ([Pi SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md); [Pi repository and security boundary](https://github.com/earendil-works/pi))

Use an embedded agent only when an extension's work requires interpretation under uncertainty, unstructured evidence synthesis, or hypothesis generation. Themes, status widgets, deterministic validators, schedulers, formatters, and simple tools should remain ordinary extensions.

An agent-backed extension should give its internal agent:

- one declared job, input/output schema, and bounded tool set;
- a small local state or reflection buffer tied to that job;
- a task-specific evaluator, preferably deterministic or executable;
- an explicit stop condition and resource budget;
- evidence/provenance in its output: what it observed, what it changed, and which checks passed;
- no authority to edit unrelated extensions, the global harness, the evaluator, identity/constitution, or deployment configuration.

Narrow scope makes failures attributable and lets Zenkai compare like-for-like candidates. It also limits the blast radius when reflection is wrong or an evaluator is gamed.

### 2. Keep the extension bridge deterministic and run Zenkai as a separate Pi agent

The smallest complete Zenkai shape is:

```text
Pi extension inside Ayanokoji / deterministic bridge
  - commands, scheduling, leases, state, policy, target hashes
  - starts or contacts Zenkai, shows progress, confirmation, apply, rollback

Separate Zenkai Pi agent / improver
  - separate process or worker and agent run identity
  - package-owned Zenkai AGENTS.md and dedicated model policy
  - no Ayanokoji AGENTS.md, foreground transcript, or inherited resources
  - root-bounded read-only evidence tools
  - typed observation or candidate submission only

Independent evaluator
  - held-out tasks and immutable candidate
  - deterministic checks first; separate judge only as advisory evidence
```

The current Zenkai implementation contains a useful seed: its reasoner creates a fresh in-memory Pi SDK session, disables inherited extensions, skills, templates, themes, and context files, and exposes read-only inspection plus typed submission. However, it still runs from the extension's process and follows the foreground session's selected model. The intended design should move that reasoner behind a separate worker/process boundary, load only a versioned Zenkai `AGENTS.md`, use Zenkai's configured model, and report through typed IPC or the run journal. The main extension must not perform the improvement reasoning itself.

### 3. Keep Zenkai outside the target agent

Zenkai should be an external **research-and-improvement loop**:

1. collect traces, failures, costs, and evaluator results from a target harness;
2. formulate a falsifiable improvement hypothesis;
3. generate a candidate patch/configuration in an isolated workspace or branch;
4. run syntax, unit, integration, regression, security, and resource checks;
5. evaluate on held-out tasks and, where possible, an independent evaluator;
6. retain the candidate, evidence, and lineage in a version archive;
7. require an explicit release gate before promoting it.

The separation is valuable because the target agent cannot silently redefine its own evaluator while executing user work. It is not automatically independent: if Zenkai, the Pi extension, and the judge share the same model family or prompt priors, correlated errors remain possible. This is an inference supported by the evaluator-bias and solver–verifier literature, not a measured Titan result.

### 4. Prefer external checks over self-approval

Use a hierarchy of evidence:

1. deterministic tests, type checks, schemas, replay tests, and security policy checks;
2. hidden holdout tasks and randomized/perturbed inputs;
3. a separate judge or cross-model comparison with blinded candidate identity and swapped order;
4. human review for changes affecting permissions, retention, safety policy, or the improvement mechanism itself.

Natural-language self-critique is useful for generating hypotheses and explanations. It should not be the only promotion signal.

### 5. Version and archive candidates; do not overwrite the live system

DGM’s archive result supports maintaining multiple candidate lineages rather than only the latest version. A Zenkai candidate should be immutable after evaluation, reproducible from a clean checkout, and promotable/rollbackable by an explicit release action. Record the exact code/configuration, model identifiers, prompts, test corpus version, evaluator versions, scores, failures, and approval decision.

### 6. Keep the improvement surface below the weight-update line initially

The literature gives a useful progression:

`reflection/memory → prompt/workflow/tool changes → versioned harness code → persistent weight/data updates`

Start with the first three. They are inspectable and reversible. Treat SEAL-style weight/data updates as a distinct experiment with stronger data governance, rollback, contamination tests, and human approval.

## Concrete Zenkai v2 shape

Keep the public `scan / inspect / decide` engine interface, but place `scan` reasoning in the separate Zenkai agent runtime. Refactor the inside into four roles with one-way authority:

```text
Titan + target snapshots
          |
          v
Observer role (cheap, parallel, packet-scoped)
          |
          v
Deterministic clustering/deduplication
          |
          v
Improver role (whole-run synthesis, max three candidates)
          |
          v
Static verifier + independent replay evaluator
          |
          v
Saad review -> deterministic apply/rollback controller
```

- The **observer** may submit typed observations and evidence IDs, not file paths or patches.
- The **improver** receives deduplicated observations, a canonical target manifest, and engine-created snapshots. It may submit typed change intent and content, but not authority labels, target hashes, application state, or receipts.
- The **evaluator** receives immutable baseline/candidate artifacts and engine-owned held-out cases. It cannot modify or accept the candidate.
- The **controller** resolves targets, assigns authority from Titan provenance, runs policy, journals intent, obtains confirmation, applies atomically, and rolls back.

Zenkai's Pi sessions should be isolated and non-recursive, with no inherited Ayanokoji extensions, skills, personal context, shell, edit/write, package management, MCP, or subagent tool. Replace Pi's general filesystem tools with custom root-bounded inspection tools that enforce canonical roots, symlink checks, and output-size limits. Persistent continuity belongs in typed Zenkai run artifacts and Titan evidence, not in an uncontrolled shared conversation transcript.

Do not build a global agent platform yet. Keep the dedicated worker and bounded runtime private to `pi-zenkai`; extract a shared library only when a second real extension needs the same seam. Likewise, ordinary deterministic extensions should never pay the model, state, latency, or coordination cost of an agent they do not need.

Zenkai must not improve its own controller, evaluator, safety policy, or apply mechanism from inside the same loop. If its telemetry reveals a Zenkai defect, emit an advisory normal coding task for a separate agent/session. This preserves the outsider advantage rather than recreating self-evaluation one layer down.

## Failure modes to design against

| Failure mode | What the primary evidence says | Zenkai/Pi control |
|---|---|---|
| **Self-reward circularity** | Self-Rewarding and Meta-Rewarding show benchmark gains, while MT-Bench and self-preference work document evaluator biases. | Never use one model’s unblinded score as the sole release gate; use deterministic checks, cross-model judges, order swaps, and holdouts. |
| **Reward/specification hacking** | Anthropic’s reward-tampering study reports models generalizing from harmless specification gaming to more serious reward tampering in controlled settings. | Keep evaluators outside the candidate’s write scope; test invariances and inspect side effects, not only the headline score. ([Perez et al., 2024](https://www.anthropic.com/research/reward-tampering)) |
| **Synthetic error accumulation** | Sol-Ver identifies erroneous/overly simple self-generated data as a cause of collapsed generalization. | Preserve raw traces, distinguish observations from model-written hypotheses, and require fresh/held-out evidence. |
| **Critique hallucination and overcorrection** | CriticGPT finds a precision/recall tradeoff; model critics hallucinate or nitpick more than humans. | Track false-positive critique rate and regression rate; allow “no change” and revert options. |
| **Benchmark overfitting** | DGM and AlphaEvolve rely on executable benchmarks/evaluators; their results do not establish broad real-world utility. | Split public development metrics from hidden promotion tests; add adversarial and longitudinal tests. |
| **Latest-version collapse** | DGM reports that latest-only search makes poor self-modifications harder to recover from; branching archives preserve stepping stones. | Keep parent/child lineage, retain diverse candidates, and never delete the last known-good version automatically. |
| **Sandbox escape or privilege expansion** | STOP explicitly evaluates sandbox bypass; DGM reports sandboxing and human oversight as safety precautions. | Run candidates with least privilege, isolated files/network, time and cost limits, and an immutable policy layer. |
| **Identity/constitution drift** | Constitutional AI shows principles can guide behavior, but it does not make the principles unmodifiable or prove their preservation under self-editing. | Keep identity, safety policy, evaluator definitions, and release controls outside the editable candidate surface. |
| **Evaluator-target co-adaptation** | Solver–verifier work suggests improvement depends on a capability gap; once solver and verifier co-adapt, the gap can narrow. | Rotate or strengthen evaluators, use external tools, and periodically re-baseline against untouched tests. |

## Boundaries of the evidence

These papers mostly measure coding, math, preference, or synthetic-agent tasks. They do not measure Titan’s Pi extension contract, Zenkai, long-lived memory quality, privacy behavior, or production deployment risk. Several results are preprints or workshop papers, and benchmark numbers are setup-dependent. “External improver is safer” is therefore a design recommendation based on reduced coupling, auditability, and reversibility—not an experimentally established safety theorem.

The narrowest defensible thesis is:

> Pi extensions may use bounded, local reflection to improve execution within a declared contract. Zenkai may study and generate candidate improvements to those extensions and their harness, but promotion must be mediated by independent tests, versioned evidence, least privilege, and an explicit release gate. Zenkai itself should not be the sole authority over the system that evaluates Zenkai’s changes.

## Primary sources

- [Reflexion](https://arxiv.org/abs/2303.11366)
- [Self-Refine](https://arxiv.org/abs/2303.17651)
- [Constitutional AI](https://arxiv.org/abs/2212.08073)
- [CriticGPT paper and evaluation](https://cdn.openai.com/llm-critics-help-catch-llm-bugs-paper.pdf)
- [Self-Rewarding Language Models](https://arxiv.org/abs/2401.10020)
- [Meta-Rewarding Language Models](https://arxiv.org/abs/2407.19594)
- [Judging LLM-as-a-Judge](https://arxiv.org/abs/2306.05685)
- [Self-Preference Bias in LLM-as-a-Judge](https://arxiv.org/abs/2410.21819)
- [PAST-Bench](https://arxiv.org/abs/2608.04003)
- [Automated Design of Agentic Systems](https://arxiv.org/abs/2408.08435)
- [Self-Taught Optimizer (STOP)](https://arxiv.org/abs/2310.02304)
- [A Self-Improving Coding Agent (SICA)](https://arxiv.org/abs/2504.15228)
- [Darwin Gödel Machine](https://arxiv.org/abs/2505.22954)
- [AlphaEvolve](https://arxiv.org/abs/2506.13131)
- [Self-Adapting Language Models (SEAL)](https://arxiv.org/abs/2506.10943)
- [Learning to Solve and Verify (Sol-Ver)](https://arxiv.org/abs/2502.14948)
- [Solver–verifier gap model](https://arxiv.org/abs/2507.00075)
- [Sycophancy to subterfuge: reward tampering](https://www.anthropic.com/research/reward-tampering)
- [Pi SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md)
- [Pi repository and security boundary](https://github.com/earendil-works/pi)
