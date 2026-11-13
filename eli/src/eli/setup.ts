import Anthropic from "@anthropic-ai/sdk";
import { createClient } from "../sharepoint/factory.js";
import { runAgent, type AgentResult } from "./agent.js";
import type { EliConfig } from "./config.js";
import { DriveReader, GoogleDriveApi, serviceAccountEmail } from "./gdrive/client.js";
import { buildSystemPrompt } from "./prompt.js";
import { driveTools } from "./tools/gdrive.js";
import { type AnyRunnableTool, sharePointTools } from "./tools/sharepoint.js";
import { webSearchTool } from "./webSearch.js";

export interface AnswerOptions {
  /** Include web search with this `max_uses` (the caller caps it at the person's remaining budget). Omit = no search. */
  webSearch?: { maxUses: number };
}

export interface EliBrain {
  /** `extraTools` are per-request tools, such as reading files attached to the current Slack thread. */
  answer: (
    messages: Anthropic.Beta.BetaMessageParam[],
    extraTools?: AnyRunnableTool[],
    options?: AnswerOptions,
  ) => Promise<AgentResult>;
  /** Startup banner lines describing what is connected. Never contains secrets. */
  describe: () => Promise<string[]>;
}

/** Wire the model, the tools and the system prompt from config. Shared by the Slack app and the CLI. */
export function createBrain(config: EliConfig): EliBrain {
  const tools: AnyRunnableTool[] = [];
  if (config.sharepoint) {
    tools.push(...sharePointTools(createClient({ sharepoint: config.sharepoint }), config.sharepoint));
  }
  if (config.gdrive) {
    const reader = new DriveReader(new GoogleDriveApi(config.gdrive.keyFile), { maxDownloadBytes: config.gdrive.maxDownloadBytes });
    tools.push(...driveTools(reader));
  }
  const ws = config.webSearch;
  const system = buildSystemPrompt({ sharepoint: !!config.sharepoint, gdrive: !!config.gdrive, webSearch: ws.enabled,
    reminders: config.reminders.enabled,
  });
  const client = new Anthropic(); // reads ANTHROPIC_API_KEY from the environment

  return {
    answer: (messages, extraTools = [], options = {}) => {
      const maxUses = ws.enabled ? (options.webSearch?.maxUses ?? 0) : 0;
      return runAgent(
        {
          client,
          model: config.model,
          effort: config.effort,
          maxTokens: config.maxTokens,
          maxIterations: config.maxIterations,
          system,
          tools: [...tools, ...extraTools],
          serverTools: maxUses > 0 ? [webSearchTool(ws, maxUses)] : [],
        },
        messages,
      );
    },
    describe: async () => {
      const sp = config.sharepoint;
      const lines = [
        `  model:           ${config.model} (effort ${config.effort})`,
        `  limits:          ${config.limits.userPerHour}/person/hour, ${config.limits.workspacePerDay}/workspace/day`,
        `  usage log:       ${config.usageLogPath}`,
      ];
      if (!sp) lines.push("  SharePoint:      off");
      else if (sp.mode === "mock") lines.push(`  SharePoint:      mock sample data (allowed: ${sp.allowedFolders.join(", ")})`);
      else {
        const who = sp.graph?.auth === "delegated" ? "signed in as you (delegated)" : "app-only";
        lines.push(`  SharePoint:      ${who}; allowed folders: ${sp.allowedFolders.join(", ")}`);
      }
      if (config.gdrive) {
        const email = await serviceAccountEmail(config.gdrive.keyFile);
        lines.push(`  Google Drive:    on; share folders with ${email ?? "(could not read the key file!)"}`);
      } else {
        lines.push("  Google Drive:    off (set ELI_GOOGLE_KEY_FILE to turn on)");
      }
      const domains = ws.allowedDomains
        ? `; only ${ws.allowedDomains.join(", ")}`
        : ws.blockedDomains
          ? `; never ${ws.blockedDomains.join(", ")}`
          : "";
      lines.push(
        ws.enabled
          ? `  web search:      on (${ws.version}); ${ws.maxUsesPerQuestion}/question, ${ws.perUserPerDay}/person/day${domains}`
          : "  web search:      off (set ELI_WEB_SEARCH=on to turn on)",
      );
      lines.push(
        config.reminders.enabled
          ? `  reminders:       on (max ${config.reminders.maxPerUser}/person, default zone ${config.reminders.defaultTimeZone})`
          : "  reminders:       off",
      );
      return lines;
    },
  };
}
