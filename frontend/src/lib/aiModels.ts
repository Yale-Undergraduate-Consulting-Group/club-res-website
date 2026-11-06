export type AiModel = {
  id: string;
  label: string;
  tier: 'opus' | 'sonnet' | 'haiku' | string;
  blurb: string;
};

export type AiModelGroup = {
  id: string;
  label: string;
  models: AiModel[];
};

export const FALLBACK_AI_GROUPS: AiModelGroup[] = [
  {
    id: 'anthropic',
    label: 'Claude on Bedrock',
    models: [
      { id: 'us.anthropic.claude-haiku-5-5', label: 'Claude Haiku', tier: 'haiku', blurb: 'Fastest and cheapest' },
      { id: 'us.anthropic.claude-sonnet-5-5', label: 'Claude Sonnet', tier: 'sonnet', blurb: 'Default for writing and analysis' },
      { id: 'us.anthropic.claude-opus-5-5', label: 'Claude Opus', tier: 'opus', blurb: "Most capable; about twice Sonnet's price" },
    ],
  },
];

export const DEFAULT_AI_MODEL_ID = 'us.anthropic.claude-sonnet-5-5';
export const AI_MODEL_STORAGE_KEY = 'yucg_ai_model';

export function catalogModelIds(groups: AiModelGroup[]): Set<string> {
  return new Set(groups.flatMap((g) => g.models.map((m) => m.id)));
}
