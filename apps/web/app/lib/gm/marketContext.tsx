'use client';
// gm端全局市场筛选(全部/美股/港股) —— 各tab共享
import { createContext, useContext, useState, ReactNode } from 'react';

export type GmMarket = 'all' | 'us' | 'hk';

const Ctx = createContext<{ market: GmMarket; setMarket: (m: GmMarket) => void }>({
  market: 'all',
  setMarket: () => {},
});

export function MarketProvider({ children }: { children: ReactNode }) {
  const [market, setMarket] = useState<GmMarket>('all');
  return <Ctx.Provider value={{ market, setMarket }}>{children}</Ctx.Provider>;
}

export const useMarket = () => useContext(Ctx);
