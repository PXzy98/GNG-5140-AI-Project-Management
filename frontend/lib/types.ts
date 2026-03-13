export type ApiMode = "real" | "mock";
export type ModuleMode = "traditional" | "llm";
export type LlmTier = "local" | "small" | "medium" | "large";
export type IngestionLlmMode = "hybrid" | "full";

export type ModuleKey = "ingestion" | "risk" | "drift" | "brief" | "priority";

export interface IngestionConfig {
  mode: ModuleMode;
  tier: LlmTier;
  llmMode: IngestionLlmMode;
}

export interface GenericModuleConfig {
  mode: ModuleMode;
  tier: LlmTier;
}

export interface DriftConfig {
  mode: "llm";
  tier: LlmTier;
}

export interface PipelineSettings {
  apiMode: ApiMode;
  apiBaseUrl: string;
  projectId: string;
  ingestion: IngestionConfig;
  risk: GenericModuleConfig;
  drift: DriftConfig;
  brief: GenericModuleConfig;
  priority: GenericModuleConfig;
}

export interface ArtifactResponse {
  artifact_id: string;
  status: string;
  detected_language: string;
  artifact_type: string;
  content_preview: string;
  extracted_tasks: Array<{
    task_id: string;
    text: string;
    owner?: string | null;
    deadline?: string | null;
  }>;
}

export interface RiskIdentifyResponse {
  risks: Array<{
    risk_id: string;
    description: string;
    risk_level: string;
    category: string;
  }>;
  analysis_method: string;
}

export interface DriftCheckResponse {
  drift_type: string;
  overall_alignment_score: number;
  alerts: Array<{
    alert_id: string;
    severity: string;
    detected_ask: string;
    suggested_action: string;
  }>;
}

export interface DailyPlanResponse {
  brief_id: string;
  date: string;
  morning_brief?: {
    summary: string;
    warnings: string[];
  } | null;
  ranked_tasks: Array<{
    task_id: string;
    name: string;
    rank: number;
    priority_level: string;
    priority_score: number;
  }>;
}

export interface PipelineRunResult {
  baselineArtifact?: ArtifactResponse;
  baselineResult?: unknown;
  updateArtifact?: ArtifactResponse;
  risk?: RiskIdentifyResponse;
  drift?: DriftCheckResponse;
  brief?: DailyPlanResponse;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  timestamp: string;
}
