import { Suspense } from 'react';
import GmHome from './GmHome';

export default function Page() {
  return (
    <Suspense fallback={<div style={{ color: '#8F9BB3', padding: 40, textAlign: 'center' }}>加载中…</div>}>
      <GmHome />
    </Suspense>
  );
}
