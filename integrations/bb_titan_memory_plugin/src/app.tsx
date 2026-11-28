import { definePluginApp, type ExperimentalThreadActionTabProps } from "@get-bb/plugin-sdk/app";
import { ExplorerPanel } from "./ui/explorer.js";
import "./ui/styles.css";

const EXPLORER_ACTION_ID = "titan-memory-explorer";

function TitanExplorerPanel({ threadId }: ExperimentalThreadActionTabProps) {
  return <ExplorerPanel key={threadId} threadId={threadId} />;
}

export default definePluginApp((app) => {
  app.slots.experimental_tab({
    context: { kind: "thread" },
    availability: "action",
    id: EXPLORER_ACTION_ID,
    title: "Titan Memory",
    icon: "Network",
    component: TitanExplorerPanel,
    layout: "flush",
  });
});
