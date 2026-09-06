import { definePluginApp, type PluginThreadPanelProps } from "@get-bb/plugin-sdk/app";
import { ExplorerPanel } from "./ui/explorer.js";
import "./ui/styles.css";

const EXPLORER_ACTION_ID = "titan-memory-explorer";

function TitanExplorerPanel({ threadId }: PluginThreadPanelProps) {
  return <ExplorerPanel threadId={threadId} />;
}

export default definePluginApp((app) => {
  app.slots.threadPanelAction({
    id: EXPLORER_ACTION_ID,
    title: "Titan Memory",
    icon: "Network",
    component: TitanExplorerPanel,
    layout: "flush",
  });
});
