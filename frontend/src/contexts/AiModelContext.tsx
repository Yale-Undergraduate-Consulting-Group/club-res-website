import { AiModelContext } from './useAiModel';
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { api } from '../api';
import {
  AI_MODEL_STORAGE_KEY,
  DEFAULT_AI_MODEL_ID,
  FALLBACK_AI_GROUPS,
  catalogModelIds,
  type AiModelGroup,
} from '../lib/aiModels';

export function AiModelProvider({ children }: { children: ReactNode }) {
  const [groups, setGroups] = useState<AiModelGroup[]>(FALLBACK_AI_GROUPS);
  const [modelId, setModelIdState] = useState(() => {
    try {
      const stored = localStorage.getItem(AI_MODEL_STORAGE_KEY);
      if (stored && catalogModelIds(FALLBACK_AI_GROUPS).has(stored)) return stored;
    } catch {
      /* ignore */
    }
    return DEFAULT_AI_MODEL_ID;
  });

  useEffect(() => {
    api.ai
      .models()
      .then((res) => {
        const nextGroups = res.groups?.length ? res.groups : FALLBACK_AI_GROUPS;
        setGroups(nextGroups);
        const ids = catalogModelIds(nextGroups);
        let stored: string | null = null;
        try {
          stored = localStorage.getItem(AI_MODEL_STORAGE_KEY);
        } catch {
          stored = null;
        }
        if (stored && ids.has(stored)) {
          setModelIdState(stored);
          return;
        }
        const next = res.default && ids.has(res.default) ? res.default : DEFAULT_AI_MODEL_ID;
        setModelIdState(next);
        try {
          localStorage.setItem(AI_MODEL_STORAGE_KEY, next);
        } catch {
          /* ignore */
        }
      })
      .catch(() => {});
  }, []);

  const setModelId = useCallback((id: string) => {
    setModelIdState(id);
    try {
      localStorage.setItem(AI_MODEL_STORAGE_KEY, id);
    } catch {
      /* ignore */
    }
  }, []);

  return (
    <AiModelContext.Provider value={{ modelId, setModelId, groups }}>
      {children}
    </AiModelContext.Provider>
  );
}
