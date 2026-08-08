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
  TurnResponse,
  World,
  WorldCreate,
} from './types';

export const queryKeys = {
  worlds: ['worlds'] as const,
  world: (id: string) => ['worlds', id] as const,
  characters: (worldId: string) => ['worlds', worldId, 'characters'] as const,
  sessions: (worldId?: string) => ['sessions', worldId ?? 'all'] as const,
  session: (id: string) => ['sessions', id] as const,
  messages: (id: string) => ['sessions', id, 'messages'] as const,
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

export function useSubmitTurn(sessionId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (action: string) =>
      api.post<TurnResponse>(`/api/v1/sessions/${sessionId}/turns`, { action }),
    onSuccess: () => {
      // A failed turn is a no-op server-side, so only success invalidates.
      client.invalidateQueries({ queryKey: queryKeys.messages(sessionId) });
      client.invalidateQueries({ queryKey: queryKeys.session(sessionId) });
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
