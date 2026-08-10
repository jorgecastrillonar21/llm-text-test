import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type {
  AiStatus,
  Character,
  CharacterCreate,
  GameSession,
  Message,
  SessionCreate,
  SessionDetail,
  SituationListRead,
  TurnResponse,
  World,
  WorldCreate,
  WorldRules,
} from './types';

export const queryKeys = {
  worlds: ['worlds'] as const,
  world: (id: string) => ['worlds', id] as const,
  worldRules: (id: string) => ['worlds', id, 'rules'] as const,
  characters: (worldId: string) => ['worlds', worldId, 'characters'] as const,
  sessions: (worldId?: string) => ['sessions', worldId ?? 'all'] as const,
  session: (id: string) => ['sessions', id] as const,
  messages: (id: string) => ['sessions', id, 'messages'] as const,
  situations: (id: string) => ['sessions', id, 'situations'] as const,
  aiStatus: ['ai-status'] as const,
};

export function useWorlds() {
  return useQuery({ queryKey: queryKeys.worlds, queryFn: () => api.get<World[]>('/api/v1/worlds') });
}

export function useWorld(id: string) {
  return useQuery({
    queryKey: queryKeys.world(id),
    queryFn: () => api.get<World>(`/api/v1/worlds/${id}`),
  });
}

export function useWorldRules(id: string) {
  return useQuery({
    queryKey: queryKeys.worldRules(id),
    // Its own endpoint because the document is ~3 KB and the world list would
    // otherwise carry a copy per row. Rules never change after creation, so this
    // is fetched once and kept.
    queryFn: () => api.get<WorldRules>(`/api/v1/worlds/${id}/rules`),
    staleTime: Infinity,
  });
}

export function useCreateWorld() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: WorldCreate) => api.post<World>('/api/v1/worlds', payload),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.worlds }),
  });
}

export function useCharacters(worldId: string) {
  return useQuery({
    queryKey: queryKeys.characters(worldId),
    queryFn: () => api.get<Character[]>(`/api/v1/worlds/${worldId}/characters`),
  });
}

export function useCreateCharacter(worldId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: CharacterCreate) =>
      api.post<Character>(`/api/v1/worlds/${worldId}/characters`, payload),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.characters(worldId) }),
  });
}

export function useSessions(worldId?: string) {
  const path = worldId ? `/api/v1/sessions?world_id=${worldId}` : '/api/v1/sessions';
  return useQuery({
    queryKey: queryKeys.sessions(worldId),
    queryFn: () => api.get<GameSession[]>(path),
  });
}

export function useSession(id: string) {
  return useQuery({
    queryKey: queryKeys.session(id),
    queryFn: () => api.get<SessionDetail>(`/api/v1/sessions/${id}`),
  });
}

export function useCreateSession() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: SessionCreate) => api.post<GameSession>('/api/v1/sessions', payload),
    onSuccess: () => client.invalidateQueries({ queryKey: ['sessions'] }),
  });
}

export function useMessages(sessionId: string) {
  return useQuery({
    queryKey: queryKeys.messages(sessionId),
    queryFn: () => api.get<Message[]>(`/api/v1/sessions/${sessionId}/messages`),
  });
}

/**
 * What the world currently has under way in this session.
 *
 * Live processes only. A session's concluded situations accumulate forever and are
 * history rather than status; the backend's `live_only` default is what this relies on.
 */
export function useSituations(sessionId: string) {
  return useQuery({
    queryKey: queryKeys.situations(sessionId),
    queryFn: () =>
      api.get<SituationListRead>(`/api/v1/sessions/${sessionId}/situations`),
  });
}

export function useSubmitTurn(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (action: string) =>
      api.post<TurnResponse>(`/api/v1/sessions/${sessionId}/turns`, { action }),
    onSuccess: () => {
      // A failed turn is a no-op server-side, so only success invalidates.
      client.invalidateQueries({ queryKey: queryKeys.messages(sessionId) });
      client.invalidateQueries({ queryKey: queryKeys.session(sessionId) });
      // A turn can start a process, and a turn's own state changes can move one.
      client.invalidateQueries({ queryKey: queryKeys.situations(sessionId) });
    },
  });
}

export function useAiStatus() {
  return useQuery({
    queryKey: queryKeys.aiStatus,
    queryFn: () => api.get<AiStatus>('/api/v1/ai/status'),
    refetchInterval: 30_000,
    retry: false,
  });
}
