// Top-level layout: five polled queries feed seven panels. Nothing here
// throws -- every panel already tolerates missing/partial/null data, so a
// slow or offline backend degrades the view instead of crashing it.

import { useQuery } from "@tanstack/react-query";
import { fetchDecisions, fetchIncidents, fetchRecommendations, fetchState, fetchStatus, isMockMode } from "./api";
import { Header } from "./components/Header";
import { Banner } from "./components/Banner";
import { KpiStrip } from "./components/KpiStrip";
import { StationsDepotsPanel } from "./components/StationsDepots";
import { RiskBoard } from "./components/RiskBoard";
import { RecommendationsPanel } from "./components/Recommendations";
import { IncidentsDecisionsPanel } from "./components/IncidentsDecisions";
import { SystemHealthPanel } from "./components/SystemHealth";
import { GameDayPanel } from "./components/GameDay";

export function App() {
  const mock = isMockMode();

  const stateQuery = useQuery({ queryKey: ["state"], queryFn: fetchState, refetchInterval: 1000 });
  const statusQuery = useQuery({ queryKey: ["status"], queryFn: fetchStatus, refetchInterval: 2000 });
  const recsQuery = useQuery({
    queryKey: ["recommendations"],
    queryFn: () => fetchRecommendations(),
    refetchInterval: 1000,
  });
  const decisionsQuery = useQuery({
    queryKey: ["decisions"],
    queryFn: () => fetchDecisions(),
    refetchInterval: 3000,
  });
  const incidentsQuery = useQuery({ queryKey: ["incidents"], queryFn: fetchIncidents, refetchInterval: 3000 });

  const state = stateQuery.data;
  const status = statusQuery.data;
  const backendUnreachable = !mock && stateQuery.isError;
  const worldPaused = (status?.world ?? state?.instance?.status) === "PAUSED";

  return (
    <div className="bg-page min-h-full pb-6">
      <Header state={state} status={status} />
      <Banner
        backendUnreachable={backendUnreachable}
        operatingMode={state?.operating_mode}
        worldPaused={worldPaused}
      />
      <KpiStrip kpis={state?.kpis} />
      <StationsDepotsPanel stations={state?.stations ?? []} depots={state?.depots ?? []} routes={state?.routes ?? []} />
      <RiskBoard risk={state?.risk ?? []} />
      <RecommendationsPanel recommendations={recsQuery.data ?? []} currentTick={state?.instance?.tick} />
      <div className="flex flex-wrap gap-3 mx-3 mb-3">
        <IncidentsDecisionsPanel incidents={incidentsQuery.data ?? []} decisions={decisionsQuery.data ?? []} />
        <SystemHealthPanel status={status} />
        <GameDayPanel />
      </div>
    </div>
  );
}
