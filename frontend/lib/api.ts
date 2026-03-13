import {
  ArtifactResponse,
  DailyPlanResponse,
  DriftCheckResponse,
  PipelineRunResult,
  PipelineSettings,
  RiskIdentifyResponse
} from "@/lib/types";

interface ApiAdapter {
  ingestText(content: string, sourceType: string, projectId: string): Promise<ArtifactResponse>;
  ingestFile(file: File, sourceType: string, projectId: string): Promise<ArtifactResponse>;
  setBaseline(projectId: string, artifactId: string): Promise<unknown>;
  identifyRisks(artifactIds: string[], projectId: string): Promise<RiskIdentifyResponse>;
  checkDrift(projectId: string, artifactId: string): Promise<DriftCheckResponse>;
  generateBrief(projectIds: string[]): Promise<DailyPlanResponse>;
  rerank(date: string): Promise<unknown>;
}

function resolveApiBase(baseUrl: string): string {
  return baseUrl.replace(/\/$/, "");
}

function createRealAdapter(settings: PipelineSettings): ApiAdapter {
  const base = resolveApiBase(settings.apiBaseUrl);

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${base}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers || {})
      }
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`API ${response.status} ${path}: ${detail}`);
    }
    return (await response.json()) as T;
  }

  return {
    ingestText: (content, sourceType, projectId) =>
      request<ArtifactResponse>("/api/v1/ingest/text", {
        method: "POST",
        body: JSON.stringify({
          content,
          source_type: sourceType,
          project_id: projectId,
          use_llm: settings.ingestion.mode === "llm",
          llm_tier: settings.ingestion.tier,
          llm_mode: settings.ingestion.llmMode
        })
      }),
    ingestFile: async (file, sourceType, projectId) => {
      const form = new FormData();
      form.append("file", file);
      form.append("source_type", sourceType);
      form.append("project_id", projectId);
      form.append("use_llm", String(settings.ingestion.mode === "llm"));
      form.append("llm_tier", settings.ingestion.tier);
      const response = await fetch(`${base}/api/v1/ingest/file`, { method: "POST", body: form });
      if (!response.ok) {
        throw new Error(`API ${response.status} /api/v1/ingest/file`);
      }
      return (await response.json()) as ArtifactResponse;
    },
    setBaseline: (projectId, artifactId) =>
      request("/api/v1/drift/baseline", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          artifact_id: artifactId,
          version: "v1",
          use_llm: true,
          llm_tier: settings.drift.tier
        })
      }),
    identifyRisks: (artifactIds, projectId) =>
      request<RiskIdentifyResponse>("/api/v1/risk/identify", {
        method: "POST",
        body: JSON.stringify({
          artifact_ids: artifactIds,
          project_id: projectId,
          use_llm: settings.risk.mode === "llm",
          llm_tier: settings.risk.tier
        })
      }),
    checkDrift: (projectId, artifactId) =>
      request<DriftCheckResponse>("/api/v1/drift/check", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          artifact_id: artifactId,
          use_llm: true,
          llm_tier: settings.drift.tier
        })
      }),
    generateBrief: (projectIds) =>
      request<DailyPlanResponse>("/api/v1/brief/generate", {
        method: "POST",
        body: JSON.stringify({
          project_ids: projectIds,
          use_llm: settings.brief.mode === "llm",
          llm_tier: settings.brief.tier,
          top_k: 10
        })
      }),
    rerank: (date) =>
      request(`/api/v1/brief/${date}/rerank`, {
        method: "POST",
        body: JSON.stringify({
          trigger: "manual",
          reason: "Triggered from stage demo UI",
          use_llm: settings.priority.mode === "llm",
          llm_tier: settings.priority.tier
        })
      })
  };
}

function randomId(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function createMockAdapter(): ApiAdapter {
  return {
    ingestText: async (content, sourceType, projectId) => {
      await sleep(300);
      return {
        artifact_id: randomId("art"),
        status: "processed",
        detected_language: /[\u00C0-\u017F]/.test(content) ? "mixed" : "en",
        artifact_type: sourceType,
        content_preview: content.slice(0, 400),
        extracted_tasks: [
          {
            task_id: randomId("task"),
            text: "Review vendor timeline update and confirm new milestones",
            owner: "PM Team",
            deadline: "2026-03-20"
          }
        ]
      };
    },
    ingestFile: async (file, sourceType) => {
      await sleep(350);
      return {
        artifact_id: randomId("art"),
        status: "processed",
        detected_language: "en",
        artifact_type: sourceType,
        content_preview: `Mock extracted content from file: ${file.name}`,
        extracted_tasks: [
          {
            task_id: randomId("task"),
            text: "Validate extracted scope section from uploaded file",
            owner: "Tech Lead",
            deadline: "2026-03-22"
          }
        ]
      };
    },
    setBaseline: async (projectId, artifactId) => {
      await sleep(280);
      return {
        baseline_id: randomId("bsl"),
        project_id: projectId,
        source_artifact_id: artifactId
      };
    },
    identifyRisks: async () => {
      await sleep(450);
      return {
        analysis_method: "llm_large",
        risks: [
          {
            risk_id: randomId("rsk"),
            description: "Vendor delay threatens migration timeline",
            risk_level: "high",
            category: "schedule"
          },
          {
            risk_id: randomId("rsk"),
            description: "No backup migration engineer identified",
            risk_level: "critical",
            category: "resource"
          }
        ]
      };
    },
    checkDrift: async () => {
      await sleep(420);
      return {
        drift_type: "scope_expansion",
        overall_alignment_score: 0.43,
        alerts: [
          {
            alert_id: randomId("alt"),
            severity: "high",
            detected_ask: "Add new DR environment setup not in baseline",
            suggested_action: "Raise change request and estimate timeline impact"
          }
        ]
      };
    },
    generateBrief: async () => {
      await sleep(500);
      return {
        brief_id: randomId("brief"),
        date: new Date().toISOString().slice(0, 10),
        morning_brief: {
          summary: "Focus on critical blockers, then resolve scope expansion requests.",
          warnings: ["Two tasks have rollover count >= 3"]
        },
        ranked_tasks: [
          {
            task_id: randomId("task"),
            name: "Escalate vendor delay and secure recovery plan",
            rank: 1,
            priority_level: "critical",
            priority_score: 95
          },
          {
            task_id: randomId("task"),
            name: "Review and approve change request for DR scope",
            rank: 2,
            priority_level: "high",
            priority_score: 86
          }
        ]
      };
    },
    rerank: async () => {
      await sleep(220);
      return { status: "ok", changes: 2 };
    }
  };
}

export function getApiAdapter(settings: PipelineSettings): ApiAdapter {
  return settings.apiMode === "mock" ? createMockAdapter() : createRealAdapter(settings);
}

export async function runPipeline(
  settings: PipelineSettings,
  input: {
    baselineText: string;
    updateText: string;
    baselineSourceType: string;
    updateSourceType: string;
  }
): Promise<PipelineRunResult> {
  const api = getApiAdapter(settings);

  const baselineArtifact = await api.ingestText(
    input.baselineText,
    input.baselineSourceType,
    settings.projectId
  );
  const baselineResult = await api.setBaseline(settings.projectId, baselineArtifact.artifact_id);

  const updateArtifact = await api.ingestText(
    input.updateText,
    input.updateSourceType,
    settings.projectId
  );
  const risk = await api.identifyRisks([updateArtifact.artifact_id], settings.projectId);
  const drift = await api.checkDrift(settings.projectId, updateArtifact.artifact_id);
  const brief = await api.generateBrief([settings.projectId]);

  return {
    baselineArtifact,
    baselineResult,
    updateArtifact,
    risk,
    drift,
    brief
  };
}
