# Copyright (C) 2023  Andrea Patrizi (AndrePatri, andreapatrizi1b6e6@gmail.com)
# 
# This file is part of OmniRoboGym and distributed under the General Public License version 2 license.
# 
# OmniRoboGym is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# 
# OmniRoboGym is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with OmniRoboGym.  If not, see <http://www.gnu.org/licenses/>.
# 
from omni.isaac.core.articulations.articulation_view import ArticulationView

import torch

import xml.etree.ElementTree as ET

from SharsorIPCpp.PySharsorIPC import LogType
from SharsorIPCpp.PySharsorIPC import Journal

class OmniRobotHomer:

    def __init__(self, 
            articulation: ArticulationView, 
            srdf_path: str, 
            backend = "torch", 
            device: torch.device = torch.device("cpu"), 
            dtype = torch.float64):

        self.torch_dtype = dtype 
        
        if not articulation.initialized:
            
            exception = f"the provided articulation is not initialized properly!"

            Journal.log(self.__class__.__name__,
                "__init__",
                exception,
                LogType.EXCEP,
                throw_when_excep = True)
                    
        self._articulation = articulation
        self.srdf_path = srdf_path

        self._device = device

        self.num_robots = self._articulation.count
        self.n_dofs = self._articulation.num_dof
        self.jnts_names = self._articulation.dof_names

        self.joint_idx_map = {}
        for joint in range(0, self.n_dofs):

            self.joint_idx_map[self.jnts_names[joint]] = joint 

        if (backend != "torch"):

            print(f"[{self.__class__.__name__}]"  + f"[{self.journal.info}]" + ": forcing torch backend. Other backends are not yet supported.")
        
        self._backend = "torch"

        self._homing = torch.full((self.num_robots, self.n_dofs), 
                        0.0, 
                        device = self._device, 
                        dtype=self.torch_dtype) # homing configuration
        
        # open srdf and parse the homing field
        
        with open(srdf_path, 'r') as file:
            
            self._srdf_content = file.read()

        try:
            self._srdf_root = ET.fromstring(self._srdf_content)
            # Now 'root' holds the root element of the XML tree.
            # You can navigate through the XML tree to extract the tags and their values.
            # Example: To find all elements with a specific tag, you can use:
            # elements = root.findall('.//your_tag_name')

            # Example: If you know the specific structure of your .SRDF file, you can extract
            # the data accordingly, for instance:
            # for child in root:
            #     if child.tag == 'some_tag_name':
            #         tag_value = child.text
            #         # Do something with the tag value.
            #     elif child.tag == 'another_tag_name':
            #         # Handle another tag.

        except ET.ParseError as e:
        
            print(f"[{self.__class__.__name__}]" + f"[{self.journal.warning}]" + ": could not read SRDF properly!!")

        # Find all the 'joint' elements within 'group_state' with the name attribute and their values
        joints = self._srdf_root.findall(".//group_state[@name='home']/joint")

        self._homing_map = {}

        for joint in joints:
            joint_name = joint.attrib['name']
            joint_value = joint.attrib['value']
            self._homing_map[joint_name] =  float(joint_value)
        
        self._assign2homing()

    def _assign2homing(self):
        
        for joint in list(self._homing_map.keys()):
            
            if joint in self.joint_idx_map:
                
                self._homing[:, self.joint_idx_map[joint]] = torch.full((self.num_robots, 1), 
                                                                self._homing_map[joint],
                                                                device = self._device, 
                                                                dtype=self.torch_dtype).flatten()
            else:

                print(f"[{self.__class__.__name__}]" + f"[{self.journal.warning}]" + f"[{self._assign2homing.__name__}]" \
                      + ": joint " + f"{joint}" + " is not present in the articulation. It will be ignored.")
                
    def get_homing(self, 
                clone: bool = False):

        if not clone:

            return self._homing
        
        else:

            return self._homing.clone()
