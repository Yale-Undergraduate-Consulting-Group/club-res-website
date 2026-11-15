import { useEffect, useState } from 'react';
import { sentimentApi } from '../api';
import AppSubnav from '../components/AppSubnav';
import GoogleTab from '../components/sentiment/GoogleTab';
import InterviewsTab from '../components/sentiment/InterviewsTab';
import RedditTab from '../components/sentiment/RedditTab';
import PageHeader from '../components/PageHeader';
import { Notice } from '../components/ui/Primitives';
import { useUrlTab } from '../lib/useUrlTab';

type Tab = 'interviews' | 'reddit' | 'google';
const TABS: readonly Tab[] = ['interviews', 'reddit', 'google'];

/** Market sentiment: sentiment from interview transcripts, Reddit and Google reviews */
export default function Sentiment() {
  const [tab, setTab] = useUrlTab<Tab>(TABS, 'interviews');
  const [configured, setConfigured] = useState<{ reddit: boolean; google: boolean } | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    sentimentApi.status().then(setConfigured).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <div className="app-workspace max-w-5xl space-y-4">
      <PageHeader title="Market sentiment" subtitle="Sentiment from interview transcripts, Reddit and Google reviews" />
      {error && <Notice tone="danger">{error}</Notice>}
      <AppSubnav
        items={[{ id: 'interviews', label: 'Interviews' }, { id: 'reddit', label: 'Reddit' }, { id: 'google', label: 'Google reviews' }]}
        active={tab}
        onChange={(id) => setTab(id as Tab)}
      />
      {tab === 'interviews' && <InterviewsTab />}
      {tab === 'reddit' && <RedditTab configured={configured ? configured.reddit : null} />}
      {tab === 'google' && <GoogleTab configured={configured ? configured.google : null} />}
    </div>
  );
}
