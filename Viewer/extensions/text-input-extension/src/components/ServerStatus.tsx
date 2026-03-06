import React, { useState, useEffect } from 'react';
import { ProgressLoadingBar } from '@ohif/ui';
import axios from 'axios';
import './Loading.css';


const ServerStatus = ({ modelIsRunning, dataIsUploading, isServerRunning, serverUrl, serviceHealth }) => {
  
  const [progress, setProgress] = useState('');
  

  useEffect(() => {
    const getServerLog = async () => {
      if (modelIsRunning){
        try {
          const response = await axios.get(`${serverUrl}/progress`);
  
          if (response.status === 200) {
            
            setProgress(response.data);
          }
        } catch (error) {
  
          console.log("Error when getting server Log:", error);
        }
      }
      else{
        console.log("Model is not running, no attempt to access server logs.")
      }

    };

    getServerLog();
    const interval = setInterval(getServerLog, 5000); // Check every 5 seconds

    return () => clearInterval(interval); // Cleanup on component unmount
  }, [modelIsRunning]);


  

  return (
  <div className="ohif-scrollbar flex flex-col">
    <div className="flex allign-center p-2 bg-primary-dark">
      <div className="bg-transparent break-all text-base text-blue-300">
        Server Status:
      </div>
      <div
        style={{
          width: '10px',
          height: '10px',
          borderRadius: '50%',
          backgroundColor: isServerRunning ? 'green' : 'red',
          marginLeft:'8px',
          marginTop:'5px',
        }}
      />
      <div
        className="allign-center p-1 "
        sytyle={{
          marginLeft:'8px',
          marginTop:'5px'
        }}
      >
      {modelIsRunning || dataIsUploading ? <div className="spinner"></div> : null}
      
      </div>

    </div>
    <div className="flex items-center gap-4 p-2 bg-primary-dark text-xs text-gray-300">
      <div className="flex items-center gap-1">
        <span>CT</span>
        <span
          style={{
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            backgroundColor: serviceHealth?.ct ? 'green' : 'red',
            display: 'inline-block',
          }}
        />
      </div>
      <div className="flex items-center gap-1">
        <span>XRay</span>
        <span
          style={{
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            backgroundColor: serviceHealth?.xray ? 'green' : 'red',
            display: 'inline-block',
          }}
        />
      </div>
    </div>
    
    <div className="text-gray-700 text-[12px] leading-[1.2] bg-black align-bottom p-2 appearance-none">
    {progress}
    </div>


  </div>
  );
};

export default ServerStatus;
