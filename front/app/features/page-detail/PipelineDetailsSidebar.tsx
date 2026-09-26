import React, { useEffect, useState } from "react";

export type SidebarTab = "timing" | "localization" | "story" | "settings";
export interface PipelineDetailsSidebarState {
  tab: SidebarTab;
  setTab: React.Dispatch<React.SetStateAction<SidebarTab>>;
  selectedRetryStage: string | null;
  setSelectedRetryStage: React.Dispatch<React.SetStateAction<string | null>>;
  isRetryingFromStage: boolean;
  setIsRetryingFromStage: React.Dispatch<React.SetStateAction<boolean>>;
  retryFromStageError: string | null;
  setRetryFromStageError: React.Dispatch<React.SetStateAction<string | null>>;
}

export const PipelineDetailsSidebar: React.FC<{
  imageId: string;
  render: (state: PipelineDetailsSidebarState) => React.ReactNode;
}> = React.memo(({ imageId, render }) => {
  const [tab, setTab] = useState<SidebarTab>("timing");
  const [selectedRetryStage, setSelectedRetryStage] = useState<string | null>(null);
  const [isRetryingFromStage, setIsRetryingFromStage] = useState(false);
  const [retryFromStageError, setRetryFromStageError] = useState<string | null>(null);

  useEffect(() => {
    setSelectedRetryStage(null);
    setIsRetryingFromStage(false);
    setRetryFromStageError(null);
  }, [imageId]);

  return render({
    tab,
    setTab,
    selectedRetryStage,
    setSelectedRetryStage,
    isRetryingFromStage,
    setIsRetryingFromStage,
    retryFromStageError,
    setRetryFromStageError,
  });
});

