import { request } from "../identity/api";
import type { SimulationRequest, SimulationResponse } from "./types";

export function simulateReasoning(
  encounterId: string,
  body: SimulationRequest,
  signal?: AbortSignal,
): Promise<SimulationResponse> {
  return request(`/encounters/${encodeURIComponent(encounterId)}/reasoning/simulate`, {
    method: "POST",
    body,
    csrf: true,
    signal,
  });
}
