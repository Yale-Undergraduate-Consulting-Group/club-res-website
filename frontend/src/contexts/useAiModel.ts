import { createContext, useContext } from 'react';
import { DEFAULT_AI_MODEL_ID, FALLBACK_AI_GROUPS, type AiModelGroup } from '../lib/aiModels';
export type AiModelContextValue = {
  modelId: string;
  setModelId: (id: string) => void;
  groups: AiModelGroup[];
};
export const AiModelContext = createContext<AiModelContextValue | null>(null);
export function useAiModel() {
  const ctx = useContext(AiModelContext);
  if (!ctx) {
    return {
      modelId: DEFAULT_AI_MODEL_ID,
      setModelId: () => {},
      groups: FALLBACK_AI_GROUPS,
    };
  }
  return ctx;
}
