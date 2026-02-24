import React from 'react';
import TextArea from './components/TextArea';

function ViewerReportPanelComponent({ servicesManager, commandsManager }) {
  return (
    <div className="ohif-scrollbar invisible-scrollbar flex flex-col">
      <TextArea servicesManager={servicesManager} commandsManager={commandsManager} />
    </div>
  );
}

export default ViewerReportPanelComponent;
