/** A survey draft, as the survey builder's server and model exchange it. */
export type SurveyQuestionType = 'single_choice' | 'multi_choice' | 'likert' | 'slider' | 'text';
export type SurveyQuestion = {
  type: SurveyQuestionType;
  text: string;
  choices?: string[];
  min?: number;
  max?: number;
  hypothesis?: string;
};
export type SurveyDraft = { name: string; questions: SurveyQuestion[] };

/**
 * Questions typed or pasted as text, no model involved. Format: an optional
 * "Survey: <name>" line; numbered questions ("1. …", "Q2) …") with an optional
 * [type] tag at the end (single, multi, agree, slider 0-10, text); answer
 * choices on the lines below, starting with "-". Lines starting with # are
 * comments. A question without a tag is single choice when it has choices,
 * open text otherwise.
 */
export function parseSurveyText(raw: string): SurveyDraft {
  let surveyName = 'Imported survey';
  const parsed: { text: string; tag: string; choices: string[] }[] = [];
  let current: (typeof parsed)[number] | null = null;
  for (const rawLine of raw.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    let match = line.match(/^survey(?:\s*name)?\s*:\s*(.+)$/i);
    if (match) {
      surveyName = match[1].trim();
      continue;
    }
    match = line.match(/^(?:q?\d+[.):]|\d+\s*-)\s*(.+)$/i);
    if (match) {
      current = { text: match[1], tag: '', choices: [] };
      const tag = current.text.match(/\[([^\]]+)\]\s*$/);
      if (tag && tag.index !== undefined) {
        current.tag = tag[1].toLowerCase();
        current.text = current.text.slice(0, tag.index).trim();
      }
      parsed.push(current);
      continue;
    }
    match = line.match(/^[-•*]\s*(.+)$/);
    if (match && current) {
      current.choices.push(match[1].trim());
      continue;
    }
    if (current && !current.choices.length) current.text += ` ${line}`;
  }
  const questions = parsed.map(({ text, tag, choices }): SurveyQuestion => {
    const range = tag.match(/(-?\d+)\s*(?:-|to)\s*(-?\d+)/);
    let type: SurveyQuestionType;
    if (/multi|check|all that apply/.test(tag)) type = 'multi_choice';
    else if (/agree|likert/.test(tag)) type = 'likert';
    else if (/slider|scale/.test(tag)) type = 'slider';
    else if (/text|open/.test(tag)) type = 'text';
    else if (/single|choice/.test(tag)) type = 'single_choice';
    else type = choices.length ? 'single_choice' : 'text';
    const question: SurveyQuestion = { type, text };
    if (type === 'single_choice' || type === 'multi_choice') question.choices = choices;
    if (type === 'slider') {
      question.min = range ? Number(range[1]) : 0;
      question.max = range ? Number(range[2]) : 10;
    }
    return question;
  });
  return { name: surveyName, questions };
}

/** Choice questions the import left without any choices. */
export function questionsMissingChoices(draft: SurveyDraft): SurveyQuestion[] {
  return draft.questions.filter((q) => (q.type === 'single_choice' || q.type === 'multi_choice') && !q.choices?.length);
}
