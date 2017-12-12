"A module to facilitate testing GPkit against fmincon"
from math import log10, floor, log
from .. import SignomialsEnabled
from ..keydict import KeyDict
from ..small_scripts import mag
from ..nomials.variables import Variable
from ..constraints.geometric_program import GeometricProgram
import numpy as np
# pylint: disable=too-many-statements,too-many-locals,too-many-branches

def generate_mfiles(model, logspace=False, algorithm='interior-point',
                    guess='ones', gradobj='on', gradconstr='on',
                    writefiles=True):
    """A method for preparing fmincon input files to run a GPkit program

    INPUTS:
        model       [GPkit model] The model to replicate in fmincon

        logspace    [Boolean] Whether to re-produce the model in logspace

        algorithm:  [string] Algorithm used by fmincon
                    'interior-point': uses the interior point solver
                    'SQP': uses the sequential quadratic programming solver

        guess:      [string] The type of initial guess used
                    'ones': One for each variable
                    'order-of-magnitude-floor': The "log-floor" order of
                                                magnitude of the GP/SP optimal
                                                solution (i.e. O(99)=10)
                    'order-of-magnitude-round': The "log-nearest" order of
                                                magnitude of the GP/SP optimal
                                                solution (i.e. O(42)=100)
                    'almost-exact-solution': The GP/SP optimal solution rounded
                                             to 1 significant figure
                    OR
                    [list] The actual values of initial guess to use

        gradconstr: [string] Include analytical constraint gradients?
                    'on': Yes
                    'off': No

        gradobj:    [string] Include analytical objective gradients?
                    'on': Yes
                    'off': No

        writefiles: [Boolean] whether or not to actually write the m files
    """
    if logspace: # Supplying derivatives not supported for logspace
        gradobj = 'off'
        gradconstr = 'off'

    # Create a new dictionary mapping variables to x(i)'s for use w/ fmincon
    i = 1
    newdict = {}
    lookup = []
    newlist = []
    original_varkeys = model.varkeys
    for key in model.varkeys:
        if key not in model.substitutions:
            newdict[key] = 'x({0})'.format(i)
            if logspace:
                newdict[key].replace('x', 'y')
            newlist += [key.str_without(["units"])]
            lookup += ['x_{0}: '.format(i) + key.str_without(["units"])]
            i += 1
    x0string = make_initial_guess(model, newlist, guess, logspace)

    cost = model.cost # needs to be before subinplace()
    constraints = model
    substitutions = constraints.substitutions
    constraints.substitutions = KeyDict()
    constraints.subinplace(substitutions)
    constraints.subinplace(newdict)
    constraints.substitutions = substitutions

    # Make all constraints less than zero, return list of clean strings
    c = [] # inequality constraints
    ceq = [] # equality constraints
    dc = [] # gradients of inequality constraints
    dceq = [] # gradients of equality constraints

    if logspace:
        for constraint in constraints:
            if isinstance(constraint.right.value, np.float64):
                if float(constraint.right.value) == 1.0:
                    expdicttuple = constraint.left.exps
                    clist = mag(constraint.left.cs)
            else:
                expdicttuple = constraint.as_posyslt1()[0].exps
                clist = mag(constraint.as_posyslt1()[0].cs)

            constraintstring = ['log(']
            for expdict, C in zip(expdicttuple, clist):
                constraintstring += ['+ {0}*exp('.format(C)]
                for k, v in expdict.iteritems():
                    constraintstring += ['+{0} * {1}'.format(v, k)]
                constraintstring += [')']
            constraintstring += [')']

            joinedconstraintstring = ' '.join(constraintstring)

            if constraint.oper == '=':
                ceq += [joinedconstraintstring]
            else:
                c += [joinedconstraintstring]
    else:
        with SignomialsEnabled():
            for constraint in constraints:
                if constraint.oper == '<=':
                    cc = constraint.left - constraint.right
                    c += [cc.str_without(["units", "models"])]
                elif constraint.oper == '>=':
                    cc = constraint.right - constraint.left
                    c += [cc.str_without(["units", "models"])]
                elif constraint.oper == '=':
                    cc = constraint.right - constraint.left
                    ceq += [cc.str_without(["units", "models"])]

                # Differentiate each constraint w.r.t each variable
                cdm = []
                for key in original_varkeys:
                    if key not in model.substitutions:
                        cd = cc.diff(newdict[key])
                        cdm += [cd.str_without("units").replace('**', '.^')]

                if constraint.oper != '=':
                    dc += [",...\n          ".join(cdm)]
                else:
                    dceq += [",...\n            ".join(cdm)]

    if not logspace:
        xpositiveconstraint = "    -x\n"
        xpositivesens = ";...\n          -eye(numel(x))"
    else:
        xpositiveconstraint = ''
        xpositivesens = ''

    # String for the constraint function .m file
    confunstr = ("function [c, ceq, DC, DCeq] = confun(x)\n" +
                 "% Nonlinear inequality constraints\n" +
                 "c = [\n    " +
                 "\n    ".join(c).replace('**', '.^') +
                 "\n{0}    ];\n\n".format(xpositiveconstraint) +
                 "ceq = [\n      " +
                 "\n      ".join(ceq).replace('**', '.^') +
                 "\n      ];\n" +
                 "if nargout > 2\n    " +
                 "DC = [\n          " +
                 ";\n          ".join(dc) +
                 "{0}\n         ]';\n    ".format(xpositivesens) +
                 "DCeq = [\n            " +
                 ";\n            ".join(dceq) +
                 "\n           ]';\n" +
                 "end")

    # Objective function (and derivatives if applicable)
    cost.subinplace(newdict)
    objdiff = []
    if logspace:
        objstring = ['log(']
        expdicttuple = cost.exps
        clist = mag(cost.cs)
        for expdict, cc in zip(expdicttuple, clist):
            objstring += ['+ {0}*exp('.format(cc)]
            for k, v in expdict.iteritems():
                objstring += ['+{0} * {1}'.format(v, k)]
            objstring += [')']
        objstring += [')']
        obj = ' '.join(objstring)
    else:
        # Differentiate the objective function w.r.t each variable
        for key in original_varkeys:
            if key not in model.substitutions:
                costdiff = cost.diff(newdict[key])
                objdiff += [costdiff.str_without(["units", "models"]).replace(
                    '**', '.^')]

        # Replace variables with x(i), make clean string using matlab power syn.
        obj = cost.str_without(["units", "models"]).replace('**', '.^')

    # String for the objective function .m file
    objfunstr = ("function [f, gradf] = objfun(x)\n" +
                 "f = " + obj + ";\n" +
                 "if nargout > 1\n" +
                 "    gradf  = [" +
                 "\n              ".join(objdiff) +
                 "];\n" +
                 "end")

    # String for main.m
    if logspace:
        fval = "exp(fval)"
        solval = "exp(x(i))"
        logsolution = ("\nfid = fopen('logsolution.txt', 'w');\n" +
                       "for i = 1:numel(x)\n" +
                       "    fprintf(fid, '%.3g\\n', x(i));\nend\n" +
                       "fclose(fid);")
    else:
        fval = "fval"
        solval = "x(i)"
        logsolution = ""

    gradobj = gradobj.replace('on', 'true')
    gradobj = gradobj.replace('off', 'false')
    gradconstr = gradconstr.replace('on', 'true')
    gradconstr = gradconstr.replace('off', 'false')

    mainfunstr = (x0string +
                  "options = optimoptions('fmincon');\n" +
                  "options.Algorithm = '{0}';\n".format(algorithm) +
                  "options.MaxFunEvals = Inf;\n" +
                  "options.MaxIter = 100000;\n" +
                  "options.SpecifyObjectiveGradient = {0};\n".format(gradobj) +
                  "options.SpecifyConstraintGradient = {0};\n".format(gradconstr) +
                  "options.CheckGradients = true;\n"
                  "tic;\n" +
                  "[x,fval, exitflag, output] = ...\n" +
                  "fmincon(@objfun,x0,[],[],[],[],[],[],@confun,options);\n" +
                  "elapsed = toc;\n" +
                  "fid = fopen('elapsed.txt', 'w');\n" +
                  "fprintf(fid, '%.1f', elapsed);\n" +
                  "fclose(fid);\n" +
                  "fid = fopen('iterations.txt', 'w');\n" +
                  "fprintf(fid, '%d', output.iterations);\n" +
                  "fclose(fid);\n" +
                  "fid = fopen('cost.txt', 'w');\n" +
                  "fprintf(fid, '%.5g', {0});\n".format(fval) +
                  "if exitflag == -2\n\tfprintf(fid, '(i)');\nend\n" +
                  "if exitflag == 0\n\tfprintf(fid, '(e)');\nend\n" +
                  "fclose(fid);\n" +
                  "fid = fopen('solution.txt', 'w');\n" +
                  "fid2 = fopen('initialguess.txt', 'w');\n" +
		  "for i = 1:numel(x)\n" +
                  "    fprintf(fid, '%.4g\\n', {0});\n".format(solval) +
                  "    fprintf(fid2, '%.3g\\n', x0(i));\nend\n" +
                  "fclose(fid);\nfclose(fid2);" + logsolution)

    if writefiles:
        # Write the constraint function .m file
        with open('confun.m', 'w') as outfile:
            outfile.write(confunstr)

        # Write the objective function .m file
        with open('objfun.m', 'w') as outfile:
            outfile.write(objfunstr)

        # Write a txt file for looking up original variable names
        with open('lookup.txt', 'w') as outfile:
            outfile.write("\n".join(lookup))

        # Write the main .m file for running fmincon
        with open('main.m', 'w') as outfile:
            outfile.write(mainfunstr)

    return obj, c, ceq, dc, dceq


def make_initial_guess(model, newlist, guess='ones', logspace=False):
    """Returns initial guess"""
    if not isinstance(guess, list) and not isinstance(guess, KeyDict):
        try:
            sol = model.solve(verbosity=0)
        except:
            sol = model.localsolve(verbosity=0)

    if guess == "ones":
        nvars = len(sol['freevariables'])
        if logspace:
            x0string = ["x0 = zeros({0},1);\n".format(nvars)]
        else:
            x0string = ["x0 = ones({0},1);\n".format(nvars)]
    else:
        x0string = ["x0 = ["]
        i = 1
        for vk in newlist:
            if guess == "almost-exact-solution":
                xf = mag(sol['freevariables'][vk])
                x0 = round(xf, -int(floor(log10(abs(xf))))) # rounds to 1sf
            elif guess == "order-of-magnitude-floor":
                xf = mag(sol['freevariables'][vk])
                x0 = 10**floor(log10(xf))
            elif guess == "order-of-magnitude-round":
                xf = mag(sol['freevariables'][vk])
                x0 = 10**round(log10(xf))
            elif isinstance(guess, list):
                x0 = guess[i-1]
            elif isinstance(guess, KeyDict):
                x0 = guess[vk]
            else:
                raise Exception("Unexpected guess type")

            if logspace:
                x0 = log(x0)
            x0string += [str(x0) + ", "]
            i += 1
        x0string += ["]';\n"]

    return "".join(x0string)
