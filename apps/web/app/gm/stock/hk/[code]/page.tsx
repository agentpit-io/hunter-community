import { GmStockDetail } from '../../../../components/gm/GmStockDetail';

export default async function Page({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  return <GmStockDetail market="HK" code={decodeURIComponent(code)} />;
}
