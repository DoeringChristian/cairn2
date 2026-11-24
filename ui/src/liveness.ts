import { useMemo } from 'react';
import { useResource } from './api';
import { useRefresh } from './refresh';

export type Liveness = { artifact_id: string; last_seen: string; heartbeat: boolean; lost: boolean };
/** When each unfinished artifact of a project was last heard from; null skips the request. */
export function useLiveness(project: string | null) {
  const pulse = useRefresh();
  const { data } = useResource<Liveness[]>(project && `/v1/liveness?project=${encodeURIComponent(project)}`, pulse);
  return useMemo(() => new Map(data?.map((item) => [item.artifact_id, item])), [data]);
}
