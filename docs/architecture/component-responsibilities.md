# Target Responsibilities

Analyzer MUST NOT generate SQL or call DB. Catalog is schema truth. Planner expresses intent, joins, metrics and constraints, not execution. Generator emits candidates only. SQLValidator validates and never modifies or executes. SecurityValidator fails closed. SemanticValidator reports ambiguity rather than silently changing intent. Repairer cannot bypass validators. QueryExecutor is the sole DB boundary. ResultAnalyzer cannot invent facts. PresentationPlanner selects safe fields and formats. AnswerGenerator phrases supplied facts only; AnswerValidator rejects unsupported claims and unsafe markup. Memory stores typed turns/provenance, not live schema truth. Router owns provider selection and budgets.

