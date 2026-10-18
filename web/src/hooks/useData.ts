"use client";
// Read-once data hooks. Each returns null while it loads and an empty list when the call fails,
// so a screen still renders without that data.
import { useEffect, useState } from "react";
import { api, type BillLine, type JobView } from "@/api/client";

function useOnce<T>(load: () => Promise<T[]>, key: string | undefined): T[] | null {
  const [data, setData] = useState<T[] | null>(null);
  useEffect(() => {
    let live = true;
    load().then((d) => live && setData(d)).catch(() => live && setData([]));
    return () => {
      live = false;
    };
  }, [key]);
  return data;
}

/** Supply lines of the QuickBooks bills, oldest first (one item, or all). */
export const useBills = (itemId?: string): BillLine[] | null => useOnce(() => api.bills(itemId), itemId);

/** The most recent orders. */
export const useJobs = (): JobView[] | null => useOnce(() => api.jobs(100), "jobs");
