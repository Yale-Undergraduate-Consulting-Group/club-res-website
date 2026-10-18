import { useEffect, useState } from 'react';
import { api, type Project } from '../api';

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.projects.list().then(setProjects).catch((e) => setError(e instanceof Error ? e.message : 'Projects are unavailable right now.'));
  }, []);
  return { projects, error };
}
