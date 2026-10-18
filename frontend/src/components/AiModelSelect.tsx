import { useAiModel } from '../contexts/useAiModel';

type AiModelSelectProps = {
  id?: string;
  className?: string;
  compact?: boolean;
};

export default function AiModelSelect({ id = 'ai-model', className = '', compact = false }: AiModelSelectProps) {
  const { modelId, setModelId, groups } = useAiModel();

  return (
    <label className={`app-ai-select-wrap ${compact ? 'app-ai-select-wrap--compact' : ''} ${className}`.trim()} htmlFor={id}>
      <span className="app-ai-select-label">{compact ? 'Claude' : 'AI model'}</span>
      <select
        id={id}
        className="app-ai-select"
        value={modelId}
        onChange={(e) => setModelId(e.target.value)}
        title="Claude on Bedrock"
      >
        {groups.map((g) => (
          <optgroup key={g.id} label={g.label}>
            {g.models.map((m) => (
              <option key={m.id} value={m.id} title={m.blurb || m.label}>
                {m.label}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </label>
  );
}
