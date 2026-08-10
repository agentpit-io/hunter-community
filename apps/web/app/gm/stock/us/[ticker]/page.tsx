import { GmStockDetail } from '../../../../components/gm/GmStockDetail';

export default async function Page({ params }: { params: Promise<{ ticker: string }> }) {
  const { ticker } = await params;
  return <GmStockDetail market="US" code={decodeURIComponent(ticker).toUpperCase()} />;
}
