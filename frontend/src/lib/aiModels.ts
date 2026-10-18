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
      { id: 'us.anthropic.claude-haiku-5-5', label: 'Claude', tier: 'haiku', blurb: 'Fast, grounded club work' },
    ],
  },
];

export const DEFAULT_AI_MODEL_ID = 'us.anthropic.claude-haiku-5-5';
export const AI_MODEL_STORAGE_KEY = 'yucg_ai_model';

export function catalogModelIds(groups: AiModelGroup[]): Set<string> {
  return new Set(groups.flatMap((g) => g.models.map((m) => m.id)));
}
