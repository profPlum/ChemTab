#!/bin/python3

# NOTE: this file is meant to replace model_seperator.py, 
# but for now original is kept as a backup 

import tensorflow as tf
from tensorflow import keras
import numpy as np
import sys, os
import pandas as pd
from main import *


#dp = DataPreparer() #Prepare the DataFrame that will be used downstream
#df = dp.getDataframe()
#dm = DataManager(df, dp) # currently passing dp eventually we want to abstract all the constants into 1 class

exprExec = PCDNNV2ExperimentExecutor()
exprExec.setModelFactory(PCDNNV2ModelFactory())
bestModel, experimentSettings = exprExec.modelFactory.openBestModel()
assert experimentSettings['ipscaler']==None # we cannot center data, as it makes transform non-linear

#dm.createTrainTestData(experimentSettings['dataSetMethod'], experimentSettings['noOfCpv'],
#                       experimentSettings['ipscaler'], experimentSettings['opscaler'])
dm = experimentSettings['data_manager']

composite_model = bestModel#keras.models.load_model(sys.argv[1])
composite_model.summary()

decomp_dir = f'./PCDNNV2_decomp'
os.system(f'rm -r {decomp_dir}; mkdir {decomp_dir}')

get_layers_by_name = lambda model: {layer.name: layer for layer in model.layers}
layers_by_name = get_layers_by_name(composite_model)

linear_embedder = layers_by_name['linear_embedding']
w = np.asarray(get_layers_by_name(linear_embedder)['linear_embedding'].weights[0])
print(f'linear embedder weights shape: {w.shape}') # shape is [53, nCPV]

def derive_Zmix_weights(df):
    import sklearn.linear_model
    Yi_cols = [col for col in df.columns if col.startswith('Yi')]
    X_data = df[Yi_cols]
    Y_data = df['Zmix']

    lm = sklearn.linear_model.LinearRegression(fit_intercept=False)
    lm.fit(X_data, Y_data)
    assert lm.score(X_data, Y_data)==1.0 # assert R^2=1 (since zmix should be linear combinatino of Yi's)

    return {k: v for k,v in zip(X_data.columns, lm.coef_)}

# convert to df to prepare for csv saving
CPV_names = [f'CPV_{i}' for i in range(w.shape[1])]
weight_df = pd.DataFrame(w, index=dm.input_data_cols, columns=CPV_names) # dm.input_data_cols is why we recreate training data

if 'zmix' in layers_by_name:
    zmix_weights = derive_Zmix_weights(dm.df) # get weights as dict, then convert to df
    zmix_weights = pd.DataFrame({'Zmix': zmix_weights.values()}, index=zmix_weights.keys())
    weight_df = pd.concat([zmix_weights, weight_df],axis=1) # checked on 10/10/21: that zmix comes first
    #CPV_names = ['zmix'] + CPV_names

weight_df.to_csv(f'{decomp_dir}/weights.csv', index=True, header=True)
linear_embedder.save(f'{decomp_dir}/linear_embedding') # usually not needed but included for completeness

# give regressor special input name that works with cpp tensorflow
regressor = layers_by_name['regressor']
input_ = keras.layers.Input(shape=regressor.input_shape[1:], name='input_1')
output = regressor(input_)

# below equations only work for minmax scaler!
assert experimentSettings['opscaler']=='MinMaxScaler'

# m & b from y=mx+b
m = dm.outputScaler.data_range_ # aka scale in RescaleLayer
assert np.all(m == (dm.outputScaler.data_max_ - dm.outputScaler.data_min_))
b = dm.outputScaler.data_min_ # aka offset in RescaleLayer
output['static_source_prediction'] = keras.layers.Rescaling(m, b, name='static_source_prediction')(output['static_source_prediction']) # TODO: only apply to static source prediction!
output['dynamic_source_prediction'] = keras.layers.Rescaling(1, name='dynamic_source_prediction')(output['dynamic_source_prediction']) # identity layer to rename properly
wrapper = keras.models.Model(inputs=input_, outputs=output, name='regressor')

#wrapper.add(keras.layers.Input(shape=regressor.input_shape[1:], name='input_1'))
#wrapper.add(regressor)
wrapper.save(f'{decomp_dir}/regressor')
