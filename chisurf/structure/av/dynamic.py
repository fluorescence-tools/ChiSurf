from __future__ import annotations
from chisurf import typing

import time
import json
import numpy as np
import tables
import numexpr as ne

import chisurf.fio
import chisurf.fio.structure.coordinates
#import chisurf.models
#import chisurf.fitting
import chisurf.parameter
import chisurf.structure

from . import fps_ as fps
from chisurf.parameter import ParameterGroup


ne.set_num_threads(
    chisurf.settings.cs_settings['n_threads']
)


def simulate_trajectory(
        d: np.ndarray,
        ds: np.ndarray,
        dg: float,
        t_max: float,
        t_step: float,
        diffusion_coefficient: float,
        slow_fact: float
) -> typing.Tuple[
    np.array,
    np.array,
    int,
    int
]:
    """
    `d` density of whole av in shape ng, ng, ng (as generated by fps library)
    `ds` density_slow of slow av (has to be same shape as fast av only different occupancy)
    dimensions = 2;         % two dimensional simulation
    tau = .1;               % time interval in seconds
    time = tau * 1:N;       % create a time vector for plotting

    k = sqrt(D * dimensions * tau);

    http://labs.physics.berkeley.edu/mediawiki/index.php/Simulating_Brownian_Motion
    """
    return fps.simulate_traj_point(
        d, ds, dg, t_max, t_step, diffusion_coefficient, slow_fact
    )


class DiffusionSimulationParameter(
    chisurf.parameter.ParameterGroup
):

    def __init__(
            self,
            t_max: float = 2000,
            t_step: float = 0.05,
            n_simulations: int = 4
    ):
        super().__init__()
        self.t_max = chisurf.parameter.Parameter(
            value=t_max,
            name='t-max'
        )
        self.t_step = chisurf.parameter.Parameter(
            value=t_step,
            name='t-step'
        )


class DiffusionSimulation(object):

    def __init__(
            self,
            dye,
            quenching_parameter,
            diffusion_simulation_parameter,
            verbose: bool = None,
    ):
        """

        :param dye:
        :param quenching_parameter:
        :param diffusion_simulation_parameter:
        :param verbose:
        """
        if verbose is None:
            verbose = chisurf.verbose
        self.verbose = verbose

        self.dye = dye
        self.quenching_parameter = quenching_parameter
        self.simulation_parameter = diffusion_simulation_parameter

        self._xyz = None
        self._collided = None
        self._dist = None
        self._av = None

    @property
    def av(self):
        return self._av

    @property
    def quenching_trajectory(self):
        collided = self.collided
        k_quench = self.quenching_parameter.k_quench
        """
        if False:
            dist = self._dist
            dist_c = 1.5
            if self.verbose:
                print("Number of frames               : %s" % collided.shape[0])
                print("Number of collisions with atoms: %s" % collided.sum())
            r = ne.evaluate('sum(k_quench * exp(-dist/1.5),axis=1)')
            return r.astype(dtype=np.float32)
        else:
        """
        return ne.evaluate('sum(k_quench * collided,axis=1)')

    @property
    def collided(self):
        if self._collided is None:
            self.run()
        return self._collided

    @property
    def collisions(self):
        return self.collided.sum() // self.collided.shape[0]

    @property
    def time_axis(self):
        return np.arange(
            self.quenching_trajectory.shape[0], dtype=np.float32
        ) * self.simulation_parameter.t_step

    @property
    def mean_xyz(self):
        mean = self._xyz.distance(axis=0)
        return mean

    @property
    def distance_to_mean(self):
        return np.linalg.norm(
            self.xyz - self.mean_xyz, axis=2
        ).flatten()

    @property
    def xyz(self):
        return self._xyz

    def save(
            self,
            filename: str,
            mode: str = 'h5',
            **kwargs
    ):
        """

        :param filename:
        :param mode:
        :param kwargs:
        :return:
        """
        if mode == 'h5':
            compression = tables.Filters(
                complib='zlib', shuffle=True, complevel=1
            )
            h5handle = tables.open_file(
                filename, mode="w", title="Test file", filters=compression
            )
            h5handle.create_array(
                '/',
                'topology',
                np.array(
                    json.dumps(self.dye.dye_definition)
                ).reshape(1),
                shape=(1,)
            )
            h5handle.create_earray(
                where='/',
                name='coordinates',
                atom=tables.Float32Atom(),
                shape=(0, self.dye.n_atoms, 3)
            )
            h5handle.create_earray(
                where='/',
                name='time',
                atom=tables.Float32Atom(),
                shape=(0,)
            )
            h5handle.create_group(
                where='/', name='fluorescence'
            )
            h5handle.create_earray(
                where='/fluorescence/',
                name='quencher_distance',
                atom=tables.Float32Atom(), shape=(0,)
            )
            # set units
            h5handle.root.time.set_attr('units', 'picoseconds')
            h5handle.root.xyz.set_attr('units', 'angstroms')
            h5handle.root.fluorescence.quencher_distance.set_attr('units', 'angstroms')

            h5handle.root.xyz.append(self.xyz)
            h5handle.root.time.append(self.time_axis)
            h5handle.close()
        elif mode == 'xyz':
            skip = kwargs.get('skip', 1)
            coordinates = self.xyz[::skip]
            n_frames = coordinates.shape[0]
            coordinates = coordinates.reshape(n_frames, 3)
            chisurf.fio.structure.coordinates.write_xyz(filename, coordinates)
        elif mode == 'npy':
            np.save(filename, self.xyz)

    def run(
            self,
            verbose: str = None,
            slow_fact: float = None,
            diffusion_coefficient: float = None,
            t_max: float = None,
            t_step: float = None,
            **kwargs
    ):
        if verbose is None:
            verbose = self.verbose
        if slow_fact is None:
            slow_fact = self.dye.sticking.slow_fact
        if diffusion_coefficient is None:
            diffusion_coefficient = self.dye.diffusion_map
        if t_max is None:
            t_max = self.simulation_parameter.t_max
        if t_step is None:
            t_step = self.simulation_parameter.t_step

        if verbose:
            print("Simulate dye-trajectory")
            print("-----------------------")
            print("Simulation time [us]: %.2f" % (t_max / 1000))
            print("Time-step [ps]: %.2f" % (t_step * 1000))
            print("Number of steps: %.2f" % int((t_max // t_step)))
            print("-----------------------")

        start = time.clock()
        av = self.dye.get_av()
        self._av = av
        traj, a, n_accepted, n_rejected = simulate_trajectory(
            av.density,
            av.density_slow,
            av.dg,
            slow_fact=slow_fact,
            t_step=t_step,
            t_max=t_max,
            diffusion_coefficient=diffusion_coefficient
        )
        n_frames = traj.shape[0]
        traj += av.x0
        self._xyz = traj.reshape((n_frames, 1, 3))

        if verbose:
            print("Accepted steps: %i" % n_accepted)
            print("Rejected steps: %i" % n_rejected)
        end = time.clock()
        if verbose:
            print("time spent: %.2gs" % (end - start))
            print("n_accepted: %s" % n_accepted)
            print("-----------------------")
            print(av)
            print("Mean dye-position: %s" % self.mean_xyz)
            print("critical_distance: %s" % self.dye.critical_distance)

        #### calculate distance to quencher ####
        quencher_xyz = self.quenching_parameter.xyz
        dye_xyz = self.xyz[:, 0, :]
        if verbose:
            print("Calculating quencher distances")
            print("Total quenching atoms: %s" % quencher_xyz.shape[0])

        collided = np.zeros((quencher_xyz.shape[0], dye_xyz.shape[0]), dtype=np.uint8)
        dist_array = np.empty((quencher_xyz.shape[0], dye_xyz.shape[0]), dtype=np.float16)
        a = self.dye.critical_distance
        for i, quencher_position in enumerate(quencher_xyz):
            dist = np.sum((dye_xyz - quencher_position)**2, axis=2)
            collided[i] = np.less(dist, a**2).T
            dist_array[i] = dist.T
        self._collided = collided.T
        self._dist = dist_array.T


class Dye(ParameterGroup):

    @property
    def simulation_grid_resolution(self):
        return self._simulation_grid_resolution.value

    @simulation_grid_resolution.setter
    def simulation_grid_resolution(
            self,
            v: float
    ):
        self._simulation_grid_resolution.value = v

    @property
    def tau0(self):
        return self._tau0.value

    @tau0.setter
    def tau0(
            self,
            v: float
    ):
        self._tau0.value = v

    @property
    def diffusion_coefficient(self):
        return self._diffusion_coefficient.value

    @diffusion_coefficient.setter
    def diffusion_coefficient(self, v):
        self._diffusion_coefficient.value = v

    @property
    def critical_distance(self):
        return self._critical_distance.value

    @critical_distance.setter
    def critical_distance(self, v):
        self._critical_distance.value = v

    @property
    def av_length(self):
        return self._av_length.value

    @av_length.setter
    def av_length(self, v):
        self._av_length.value = v

    @property
    def av_radius(self):
        return self._av_radius.value

    @av_radius.setter
    def av_radius(self, v):
        self._av_radius.value = v

    @property
    def av_width(self):
        return self._av_width.value

    @av_width.setter
    def av_width(self, v):
        self._av_width.value = v

    @property
    def av_parameter(self):
        p = dict()
        p['linker_length'] = self.av_length
        p['linker_width'] = self.av_width
        p['radius1'] = self.av_radius
        return p

    @av_parameter.setter
    def av_parameter(self, d):
        self.av_length = d['linker_length']
        self.av_width = d['linker_width']
        self.av_radius = d['radius1']

    @property
    def dye_definition(self):
        return chisurf.structure.av.dye_definition[self.dye_name]

    @property
    def dye_name(self):
        return self._dye_name

    @dye_name.setter
    def dye_name(self, v):
        self._dye_name = v
        self.update_parameter()

    @property
    def n_atoms(self):
        json_topology = self.dye_definition
        n_atoms = chisurf.structure.count_atoms(json_topology)
        return n_atoms

    def get_av(self, **kwargs):
        structure = kwargs.get('structure', self.structure)
        sticking = kwargs.get('sticking', self.sticking)

        av = chisurf.structure.av.ACV(
            structure=structure,
            residue_seq_number=self.attachment_residue,
            atom_name=self.attachment_atom,
            chain_identifier=self.attachment_chain,
            simulation_grid_resolution=self.simulation_grid_resolution,
            save_av=False,
            **self.av_parameter
        )
        av.calc_acv(slow_centers=sticking.slow_center,
                    slow_radius=sticking.slow_radius,
                    verbose=self.verbose)
        return av

    def update_parameter(self):
        self.av_length = self.dye_definition['av_length']
        self.av_width = self.dye_definition['av_linker_width']
        self.av_radius = self.dye_definition['av_radius1']
        self.diffusion_coefficient = self.dye_definition['diffusion_coefficient']
        self.critical_distance = self.dye_definition['quenching_distance']

    def __init__(
            self,
            sticking,
            **kwargs
    ):
        ParameterGroup.__init__(self)
        self.verbose = kwargs.get('verbose', chisurf.verbose)
        self.sticking = sticking
        self.structure = sticking.structure
        self.model = kwargs.get('model', None)

        self._critical_distance = chisurf.parameter.Parameter(
            value=7.0,
            name='RQ'
        )
        self._diffusion_coefficient = chisurf.parameter.Parameter(
            value=5.0,
            name='D[A2/ns]'
        )
        self._tau0 = chisurf.parameter.Parameter(
            value=4.0,
            name='tau0[ns]'
        )
        self._simulation_grid_resolution = chisurf.parameter.Parameter(
            value=0.5,
            name='grid spacing'
        )

        self._av_length = chisurf.parameter.Parameter(
            name='L',
            value=20.0
        )
        self._av_width = chisurf.parameter.Parameter(
            name='W',
            value=0.5
        )
        self._av_radius = chisurf.parameter.Parameter(
            name='R',
            value=3.0
        )

        self.attachment_residue = kwargs.get('attachment_residue', None)
        self.attachment_atom = kwargs.get('attachment_atom', None)
        self.attachment_chain = kwargs.get('attachment_chain', None)

        self.critical_distance = kwargs.get('critical_distance', 5.0)
        self.diffusion_coefficient = kwargs.get('diffusion_coefficient', 10.0)
        self.tau0 = kwargs.get('tau0', 4.0)

        self.av_length = kwargs.get('av_length', 20.0)
        self.av_width = kwargs.get('av_width', 0.5)
        self.av_radius = kwargs.get('av_radius', 1.5)
        self.simulation_grid_resolution = kwargs.get(
            'simulation_grid_resolution',
            0.5
        )

        dye_name = str(kwargs.get('dye_name', None))
        if dye_name in chisurf.structure.av.dye_names[0]:
            self.dye_name = self._dye_name


class Sticking(ParameterGroup):

    @property
    def slow_fact(self):
        """Slowing factor of the dye close to a slow-center. The diffusion
        coefficient is multiplied by this value.
        """
        return self._slow_fact.value

    @slow_fact.setter
    def slow_fact(self, v):
        self._slow_fact.value = v

    @property
    def slow_radius(self):
        """The radius around the C-alpha atoms which is considered as slow part
        of the accessible volume
        """
        return self._slow_radius.value

    @slow_radius.setter
    def slow_radius(self, v):
        self._slow_radius.value = v

    @property
    def slow_center(self):
        """The location of the slow part of the accessible volume.
        """
        coordinates = self.structure.atoms['xyz']
        if self.sticky_mode == 'surface' or self.quenching_parameter is None:
            slow_atoms = np.where(self.structure.atoms['atom_name'] == 'CA')[0]
            coordinates = coordinates[slow_atoms]
        elif self.sticky_mode == 'quencher':
            coordinates = self.quenching_parameter.xyz
        return coordinates

    @property
    def sticky_mode(self):
        """If this value is set to `quencher` only the quencher slow down the
        dye if it is `surface` the C-beta
        atoms of all amino-acids slow down the dye. If the `quencher` reading_routine
        is used the `quenching_parameter`
        parameter has to be passed upon initialization of the class.
        """
        return self._sticky_mode

    @sticky_mode.setter
    def sticky_mode(self, v):
        self._sticky_mode = v

    def __init__(
            self,
            fit: chisurf.fitting.fit.Fit,
            structure: chisurf.structure.Structure,
            quenching_parameter=None,
            **kwargs
    ):
        """

        :param structure: Structure
        :param quenching_parameter:
        :param kwargs:
        """
        super().__init__(fit, **kwargs)
        self.verbose = kwargs.get('verbose', chisurf.verbose)
        self.quenching_parameter = quenching_parameter
        self.structure = structure
        self.model = kwargs.get('model', None)
        self._slow_radius = chisurf.parameter.Parameter(
            name='Rs',
            value=kwargs.get('slow_radius', 8.5)
        )
        self._slow_fact = chisurf.parameter.Parameter(
            name='slow fact',
            value=kwargs.get('slow_fact', 0.1)
        )
        # sticking reading_routine is either surface quencher
        self._sticky_mode = kwargs.get('sticky_mode', 'surface')


class ProteinQuenching(ParameterGroup):

    @property
    def kQ_scale(self):
        return self._k_quench_scale.value

    @kQ_scale.setter
    def kQ_scale(self, v):
        self._k_quench_scale.value = v

    @property
    def all_atoms_quench(self):
        return self._all_atoms_quench

    @all_atoms_quench.setter
    def all_atoms_quench(self, v):
        self._all_atoms_quench = bool(v)

    @property
    def excluded_atoms(self):
        return self._excluded_atoms

    @excluded_atoms.setter
    def excluded_atoms(self, v):
        self._excluded_atoms = v

    @property
    def all_atoms_quench(self):
        self._all_atoms_quench

    @all_atoms_quench.setter
    def all_atoms_quench(self, v):
        self._all_atoms_quench = bool(v)

    @property
    def quencher(self):
        return self._quencher

    @quencher.setter
    def quencher(self, v):

        q_new = dict()
        atoms = self.structure.atoms
        for residue_key in v:
            if self.all_atoms_quench:
                atoms_idx = np.where(atoms['res_name'] == residue_key)[0]
            else:
                atoms_idx = np.where(
                    (atoms['res_name'] == residue_key) & (atoms['atom_name'] == 'CB')
                )[0]
            q_new[residue_key] = {
                'rate': v[residue_key]['rate'],
                'atoms': list(
                    set(
                        atoms[atoms_idx]['atom_name']
                    ).difference(self._excluded_atoms)
                ),
            }
        v = q_new

        # determine atom-indices and coordinates
        for residue_key in v:
            atoms_idx = []
            for atom_name in v[residue_key]['atoms']:
                idx = np.where(
                    (atoms['res_name'] == residue_key) & (self.structure.atoms['atom_name'] == atom_name)
                )[0]
                atoms_idx += list(idx)
            v[residue_key]['coordinates'] = self.structure.atoms[atoms_idx]['xyz']
            v[residue_key]['atom_idx'] = np.array(atoms_idx, dtype=np.int32)
        self._quencher = v

    @property
    def k_quench(self):
        """An array associating to each atom a quenching rate
        """
        r = np.hstack([
            [self.quencher[residue_key]['rate']] * len(self.quencher[residue_key]['atom_idx'])
            for residue_key in self.quencher
        ])
        re = r * self.kQ_scale
        return re.astype(np.float32)

    @property
    def xyz(self):
        """Coordinates of the quenching atoms
        """
        atom_idx = np.hstack([
            self.quencher[residue_key]['atom_idx']
            for residue_key in self.quencher
        ])
        return self.structure.xyz[atom_idx]

    def __str__(self):
        s = ParameterGroup.__str__(self)
        s += "\tstructure: %s\n" % self.structure.name
        s += "\tquencher:\n"
        for q in self.quencher:
            s += "\t\t%s: %s\n" % (q, self.quencher[q])
        s += "\tall atoms of aa quench: %s\n" % self.all_atoms_quench
        s += "\texcluded atoms        : %s\n" % self._excluded_atoms
        s += "\tn of quenching atoms  : %s\n" % self.xyz.shape[0]
        return s

    def __init__(self, structure, **kwargs):
        self.verbose = kwargs.get('verbose', chisurf.verbose)
        self.structure = structure

        self._quencher = None
        self._all_atoms_quench = kwargs.get('all_atoms_quench', False)
        self._k_quench_scale = chisurf.parameter.Parameter(
            value=kwargs.get('quench_scale', 0.01),
            name='kQ scale'
        )
        self._excluded_atoms = kwargs.get(
            'excluded_atoms',
            set(['CA', 'C', 'N', 'HA'])
        )
        self.quencher = kwargs.get('quenching_amino_acids',
                                   {
                                       'MET': {
                                           'atoms': ['CB'],
                                           'rate': 1.7,
                                           'atom_idx': []
                                       },
                                       'TRP': {
                                           'atoms': ['CB'],
                                           'rate': 3.5,
                                           'atom_idx': []
                                       },
                                       'TYR': {
                                           'atoms': ['CB'],
                                           'rate': 3.5,
                                           'atom_idx': []
                                       },
                                       'HIS': {
                                           'atoms': ['CB'],
                                           'rate': 1.5,
                                           'atom_idx': []
                                       },
                                       'PRO': {
                                           'atoms': ['CB'],
                                           'rate': 1.5,
                                           'atom_idx': []
                                       }
                                   }
        )


